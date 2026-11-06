"""API va media jarayonlari orasidagi qisqa signallar (Redis orqali).

Video (WS hub, HLS/WHEP proksi) alohida `media` jarayonida ishlaydi — u API bilan
bitta event loop'da bo'lsa ko'p tomoshabinda panel osilib qolardi (o'lchandi:
40 tomoshabinda oddiy API so'rovi 5-12 s). Ajratilgandan keyin ikki jarayon bir-
birining xotirasini ko'rmaydi; quyidagi uch narsa shu yerdan o'tadi:

* publisher o'lgan  — API publisher jarayonini kuzatadi; WS hub oflayn kamerani
  90 s kutmasdan darhol "oqim yo'q" deydi ([[manager.publisher_dead]]).
* tomoshabin kutmoqda — hub manbani kutayotganda MediaMTX'da hali reader yo'q;
  API'ning idle-reaper'i bulutga ulanayotgan publisher'ni o'ldirmasin.
* hub'da kalit kadr bor — `/raw` darhol ochiladimi ([[camera_streams.choose_raw]]).

Redis yo'q/xato bo'lsa hammasi "bilmayman" (False) qaytaradi — bitta jarayonli
rejimda chaqiruvchilar baribir mahalliy holatni ham tekshiradi.
"""
from __future__ import annotations

import logging

log = logging.getLogger("vizora.signals")

_redis = None

_DEAD = "cs:pubdead:"
_VIEW = "cs:viewer:"
_KEY = "cs:hubkey:"

DEAD_TTL = 600        # o'lgan publisher belgisi (yangi publisher uni o'chiradi)
VIEW_TTL = 30         # hub har 5 s da yangilaydi
KEY_TTL = 40          # hub kalit kadr olganda yangilaydi; hub yopilsa o'chiriladi


def init(redis) -> None:
    global _redis
    _redis = redis


def _ok() -> bool:
    return _redis is not None


async def _set(key: str, ttl: int) -> None:
    if not _ok():
        return
    try:
        await _redis.set(key, "1", ex=ttl)
    except Exception as exc:  # noqa: BLE001
        log.debug("signal yozilmadi %s: %s", key, exc)


async def _del(key: str) -> None:
    if not _ok():
        return
    try:
        await _redis.delete(key)
    except Exception as exc:  # noqa: BLE001
        log.debug("signal o'chirilmadi %s: %s", key, exc)


async def _has(key: str) -> bool:
    if not _ok():
        return False
    try:
        return bool(await _redis.exists(key))
    except Exception as exc:  # noqa: BLE001
        log.debug("signal o'qilmadi %s: %s", key, exc)
        return False


# --- publisher holati (API yozadi, media o'qiydi) -------------------------------
async def publisher_started(stream_path: str) -> None:
    await _del(_DEAD + stream_path)


async def publisher_exited(stream_path: str) -> None:
    await _set(_DEAD + stream_path, DEAD_TTL)


async def publisher_dead(stream_path: str) -> bool:
    return await _has(_DEAD + stream_path)


# --- tomoshabin (media yozadi, API reaper o'qiydi) ------------------------------
async def touch_viewer(base_path: str) -> None:
    await _set(_VIEW + base_path, VIEW_TTL)


async def viewer_recent(base_path: str) -> bool:
    return await _has(_VIEW + base_path)


# --- hub kalit kadri (media yozadi, API o'qiydi) --------------------------------
async def hub_keyframe(path: str, present: bool) -> None:
    if present:
        await _set(_KEY + path, KEY_TTL)
    else:
        await _del(_KEY + path)


async def hub_has_keyframe(path: str) -> bool:
    return await _has(_KEY + path)
