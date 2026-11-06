"""Tashqi API mijoz qisqa vaqtda nechta HAR XIL kamerani uyg'ota olishi.

Iste'molchi oqimi H.264 talab qiladi (HLS/WebRTC), ya'ni bulut kamera uchun to'liq
rejimdagi publisher — ~1 yadro/kamera. Cheklovsiz bitta API kalit 160 kameraning
hammasini birdan `/stream` qilib serverni (va bulut hisoblarini) bo'g'a olardi
(audit 2026-11-03, Low). Kvota: oxirgi `consumer_camera_window_seconds` ichida
ko'pi bilan `consumer_max_cameras` ta TURLI kamera; o'sha kamerani qayta so'rash
kvotani yemaydi (pleyer token yangilashi, qayta ulanish).

Hisob Redis'da (ZSET, bir nechta uvicorn worker bo'lsa ham umumiy). Redis xato
bersa — ruxsat (oqim xizmati kvota hisobi uchun to'xtamasin).
"""
from __future__ import annotations

import logging
import time

from vizora.core.config import settings

log = logging.getLogger("vizora.quota")


async def allow_camera(redis, client_id, camera_id) -> bool:
    if redis is None or settings.consumer_max_cameras <= 0:
        return True
    key = f"cq:{client_id}"
    now = time.time()
    window = settings.consumer_camera_window_seconds
    try:
        await redis.zremrangebyscore(key, 0, now - window)
        if await redis.zscore(key, str(camera_id)) is None:
            if await redis.zcard(key) >= settings.consumer_max_cameras:
                log.warning("API mijoz %s kamera kvotasiga yetdi (%d / %ds)",
                            client_id, settings.consumer_max_cameras, window)
                return False
        await redis.zadd(key, {str(camera_id): now})
        await redis.expire(key, window)
    except Exception as exc:  # noqa: BLE001
        log.debug("kvota hisobi ishlamadi (ruxsat berildi): %s", exc)
    return True
