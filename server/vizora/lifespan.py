"""Ilova hayot sikli: ishga tushishdagi tekshiruvlar, boshlang'ich holat va fon vazifalari.

`main.py` faqat FastAPI'ni yig'adi; nima ishga tushishi va qanday to'xtashi shu yerda.
Fon vazifalari bitta ro'yxatda — yangisini qo'shish uchun `_background_jobs()` ga
bitta qator kifoya, to'xtatish avtomatik."""
import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Coroutine
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI
from sqlalchemy import func, select

from vizora.core.config import settings
from vizora.core.db import SessionLocal, engine
from vizora.core.security import hash_password
from vizora.models.camera import Camera
from vizora.models.client import User

log = logging.getLogger("vizora")


def _private_files() -> None:
    """Ilova yaratadigan fayllar faqat egasiga o'qiladi (0600/0700).

    `EZVIZ_TOKEN_DIR` da bulut sessiya tokenlari, VTM keshi (qurilma ro'yxati) va
    kamera snapshotlari turadi — ilgari 0644 bilan yozilardi (audit 2026-11-03).
    umask publisher subprocess'lariga ham meros o'tadi; avval yozilganlari tuzatiladi."""
    import os

    os.umask(0o077)
    d = settings.ezviz_token_dir
    try:
        os.makedirs(d, exist_ok=True)
        os.chmod(d, 0o700)
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p) and not os.path.islink(p):
                os.chmod(p, 0o600)
    except OSError as exc:
        log.warning("EZVIZ papkasi huquqlarini tuzatib bo'lmadi: %s", exc)


def _check_secrets() -> None:
    """Production'da default sirlar bilan ishga tushishni rad etadi."""
    insecure = settings.insecure_defaults()
    if insecure:
        msg = "Xavfsiz bo'lmagan default qiymatlar: " + ", ".join(insecure)
        if settings.is_production:
            raise RuntimeError(msg + " — production'da albatta o'zgartiring")
        log.warning("%s (dev rejimida ruxsat)", msg)
    # isup-bridge ichki API'si tokensiz bo'lsa Docker tarmog'idagi istalgan konteyner
    # kalitlarni almashtira oladi (PUT /keys). Bridge ishlatilmasligi ham mumkin, shuning
    # uchun ishga tushishni to'xtatmaymiz — faqat ogohlantiramiz.
    if settings.is_production and settings.isup_bridge_url and not settings.isup_bridge_token:
        log.warning("XAVFSIZLIK: ISUP_BRIDGE_TOKEN berilmagan — isup-bridge API'si himoyasiz")
    if settings.is_production and settings.trusted_proxy_count == 0:
        log.warning("XAVFSIZLIK: TRUSTED_PROXY_COUNT=0 — proksi ortida login limiti HAMMA "
                    "foydalanuvchi uchun bitta IP bo'yicha ishlaydi (DEPLOY.md §3)")


async def ensure_default_admin() -> None:
    """Birinchi ishga tushishda admin yo'q bo'lsa, env'dan yaratadi."""
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        if count == 0:
            db.add(
                User(
                    username=settings.admin_username,
                    password_hash=hash_password(settings.admin_password),
                    role="admin",
                )
            )
            await db.commit()
            log.warning(
                "Standart admin yaratildi: '%s' (production'da parolni o'zgartiring!)",
                settings.admin_username,
            )


async def sync_camera_paths() -> None:
    """Faol kameralar uchun MediaMTX path'larini (qayta) yaratadi."""
    from vizora.media import mediamtx

    async with SessionLocal() as db:
        cams = list(
            (await db.execute(select(Camera).where(Camera.enabled.is_(True)))).scalars().all()
        )
    ok = 0
    for cam in cams:
        if cam.is_ezviz:
            await mediamtx.safe_ensure_path(cam.stream_path)
            ok += 1
            continue
        try:
            await mediamtx.apply_rtsp_paths(cam)
        except Exception:  # noqa: BLE001
            log.warning(
                "Kamera %s RTSP'sini ochib bo'lmadi (AES kalit o'zgargan bo'lishi mumkin) — o'tkazib yuborildi",
                cam.id,
            )
            continue
        ok += 1
    if cams:
        log.info("%d/%d kamera uchun MediaMTX path sozlandi", ok, len(cams))


def _background_jobs(redis: aioredis.Redis) -> dict[str, Coroutine[Any, Any, None]]:
    """Butun ilova umri davomida ishlaydigan fon vazifalari (nom -> korutina)."""
    from vizora.services.probe import run_probe_loop
    from vizora.sources import isup
    from vizora.sources.ezviz.manager import (
        run_always_warm,
        run_fault_checker,
        run_snapshot_warmer,
        run_supervisor,
        run_warm_keeper,
    )

    return {
        # Online/offline probe sikli
        "probe": run_probe_loop(redis),
        # EZVIZ on-demand supervisor (idle-reaper + token refresh)
        "ezviz-supervisor": run_supervisor(),
        # Hik kameralarni "issiq" tutuvchi adaptiv keeper (hik_keep_warm)
        "warm-keeper": run_warm_keeper(),
        # TANLANGAN kameralar (ALWAYS_WARM_CAMERA_IDS) — doim tayyor, bir zumda ochiladi
        "always-warm": run_always_warm(),
        # Grid ochilganda qora kvadrat emas, oxirgi kadr ko'rinsin
        "snapshot-warmer": run_snapshot_warmer(),
        # "Oqim yo'q" kategoriyasidagi kameralarni vaqti-vaqti bilan qayta sinaydi
        "fault-checker": run_fault_checker(),
        # ISUP: qaysi oqim ochiq turishini isup-bridge'ga yuborib turadi
        "isup-sync": isup.run_sync(),
    }


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ishga tushish / to'xtash hayot sikli.

    `APP_ROLE=media` — faqat video (WS hub, HLS/WHEP proksi): fon vazifalari,
    standart admin va MediaMTX sinxroni API jarayonida qoladi (ikki nusxada
    ishlasa publisher'lar ikki marta boshqarilardi). API bilan bog'lanish —
    Redis signallari ([[media.signals]])."""
    from vizora.media import signals

    _check_secrets()
    _private_files()

    # Redis ulanishi (holat + pub/sub uchun)
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    signals.init(app.state.redis)
    log.info("Redis ulandi: %s (rol: %s)", settings.redis_url, settings.app_role)

    tasks: list[asyncio.Task] = []
    if settings.runs_api:
        await ensure_default_admin()

        # ffmpeg enkoderini (GPU/CPU) OLDINDAN aniqlab keshlaymiz. Sinov kodlash ~0.7s
        # oladi; busiz uni har publisher subprocess'i o'zi bajarib, birinchi kamera
        # ochilishini shuncha sekinlashtirardi. Bloklamaymiz — thread'da.
        from vizora.media import ffmpeg_hw

        asyncio.create_task(asyncio.to_thread(ffmpeg_hw.warm))

        # Mavjud (faol) kameralar uchun MediaMTX path'larini tiklash
        await sync_camera_paths()

        tasks = [asyncio.create_task(coro, name=name)
                 for name, coro in _background_jobs(app.state.redis).items()]
    app.state.background_tasks = tasks

    yield

    for t in tasks:
        t.cancel()
    for t in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await t

    from vizora.media import ws_stream
    from vizora.sources.ezviz import manager as ezviz_manager

    if settings.runs_api:
        await ezviz_manager.stop_all()
    await ws_stream.close_all()  # WS xom oqim hub'lari (RTSP o'quvchilar) yopiladi
    await app.state.redis.aclose()
    await engine.dispose()
    log.info("Toza yopildi.")
