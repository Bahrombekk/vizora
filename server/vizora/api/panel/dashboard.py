"""Boshqaruv paneli uchun yig'ma hisoblar (to'liq ro'yxatni yuklamasdan)."""
import contextlib
import time
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.ezviz_account import EzvizAccount
from vizora.models.zone import Zone
from vizora.media import mediamtx
from vizora.services import stats

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("/summary")
async def summary(db: Db, _: Admin) -> dict:
    async def count(model) -> int:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    by_status = dict(
        (await db.execute(select(Camera.status, func.count()).group_by(Camera.status))).all()
    )
    by_source = dict(
        (await db.execute(select(Camera.source_type, func.count()).group_by(Camera.source_type))).all()
    )
    return {
        "branches": await count(Branch),
        "zones": await count(Zone),
        "cameras": await count(Camera),
        "ezviz_accounts": await count(EzvizAccount),
        "ezviz": by_source.get("ezviz", 0),
        "rtsp": by_source.get("rtsp", 0),
        "online": by_status.get("online", 0),
        "offline": by_status.get("offline", 0),
        "error": by_status.get("error", 0),
        # ALOHIDA KATEGORIYA: tarmoqda bor-u video bermaydi ([[probe.no_stream]]).
        "no_stream": by_status.get("no_stream", 0),
    }


@router.get("/history")
async def history(request: Request, _: Admin, range: str = "24h", tz: int = 0) -> dict:  # noqa: A002
    """Onlayn kameralar soni tarixi (probe har daqiqada namuna oladi)."""
    # tz — brauzerning UTC farqi (daqiqa, Toshkent = 300): kunlik ustunlar mahalliy yarim tundan
    return await stats.history(request.app.state.redis, range, max(-840, min(840, tz)))


@router.get("/events")
async def events(request: Request, db: Db, _: Admin, limit: int = 8) -> list[dict]:
    """Probe aniqlagan so'nggi holat o'zgarishlari, kamera nomi bilan."""
    evs = await stats.recent_events(request.app.state.redis, min(max(limit, 1), 50))
    ids = set()
    for e in evs:
        with contextlib.suppress(ValueError, KeyError):
            ids.add(uuid.UUID(e["camera_id"]))
    names = {}
    if ids:
        rows = (await db.execute(select(Camera.id, Camera.name).where(Camera.id.in_(ids)))).all()
        names = {str(i): n for i, n in rows}
    return [
        {**e, "name": names.get(e.get("camera_id"))}
        for e in evs if e.get("camera_id") in names   # o'chirilgan kameralar tashlanadi
    ]


@router.get("/health")
async def health(request: Request, db: Db, _: Admin) -> dict:
    """Xizmatlar holati — Dashboard "Tizim holati" kartasi uchun HAQIQIY tekshiruv."""
    redis = request.app.state.redis
    redis_ok = False
    probe_age = None
    with contextlib.suppress(Exception):
        redis_ok = bool(await redis.ping())
        at = await redis.get(stats.PROBE_KEY)
        if at:
            probe_age = round(time.time() - float(at))
    mtx_ok = False
    with contextlib.suppress(Exception):
        async with httpx.AsyncClient(base_url=settings.mediamtx_api_url, timeout=3.0,
                                     auth=mediamtx.api_auth()) as c:
            mtx_ok = (await c.get("/v3/config/global/get")).status_code == 200
    accounts = (await db.execute(select(func.count()).select_from(EzvizAccount))).scalar_one()
    # Probe odatda har `probe_interval_seconds` da ishlaydi; 4 baravar kechiksa — to'xtagan.
    probe_ok = probe_age is not None and probe_age < settings.probe_interval_seconds * 4 + 60
    return {
        "database": True,   # bu so'rov bazaga murojaat qilib keldi
        "redis": redis_ok,
        "mediamtx": mtx_ok,
        "probe": probe_ok,
        "probe_age": probe_age,
        "ezviz_accounts": accounts,
    }
