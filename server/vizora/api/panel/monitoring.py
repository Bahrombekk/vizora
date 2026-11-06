"""Jonli kamera holati — snapshot (REST) + real-time push (SSE).

probe.py online/offline o'zgarishlarini Redis `camera:status` kanaliga e'lon
qiladi. Bu yerda:
  * GET /monitoring/cameras  — barcha kameralar holati (boshlang'ich snapshot).
  * GET /monitoring/stream   — Server-Sent Events: status o'zgarishlari darhol
                               admin brauzeriga oqib boradi (poll kerak emas).
SSE EventSource bilan ishlatiladi — HttpOnly cookie (vz_admin) avtomatik yuboriladi.
"""
import contextlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.sources.ezviz import manager as ezviz_manager
from vizora.services import stats
from vizora.services.probe import STATUS_CHANNEL

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
# Publisher logida pyezvizapi/ffmpeg chiqishi (URL, sessiya) bo'ladi — faqat admin.
AdminOnly = Annotated[CurrentUser, Depends(require_role("admin"))]
Db = Annotated[AsyncSession, Depends(get_db)]

# SSE heartbeat oralig'i (s) — disconnect aniqlash + proksi timeoutdan saqlanish.
_HEARTBEAT_SECONDS = 15.0


@router.get("/cameras")
async def cameras_status(db: Db, _: Admin) -> list[dict]:
    """Barcha kameralar holati (boshlang'ich snapshot). Filial/hudud ixtiyoriy."""
    stmt = (
        select(Camera, Zone, Branch)
        .outerjoin(Zone, Camera.zone_id == Zone.id)
        .outerjoin(Branch, Zone.branch_id == Branch.id)
        .order_by(Camera.status, Camera.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "enabled": c.enabled,
            "source_type": c.source_type,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "zone_name": f.name if f else None,
            "branch_name": e.name if e else None,
            "location": f.location if f else None,
        }
        for c, f, e in rows
    ]


@router.get("/system")
async def system_status(request: Request, _: Admin) -> dict:
    """Server resurslari (CPU/RAM, oxirgi ~1 soat tarixi) va faol oqimlar.

    Tarix probe siklida yoziladi ([[stats.sys_sample]]) — sahifa ochilishi bilan
    kichik grafiklar to'la bo'ladi, brauzer o'zi yig'ishini kutmaydi."""
    hist = await stats.sys_history(request.app.state.redis)
    snap = ezviz_manager.warm_snapshot()
    readers = await ezviz_manager._path_readers()
    paths = snap.get("stream_paths", {})
    uptime = None
    with contextlib.suppress(OSError, ValueError):
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = int(float(f.read().split()[0]))
    return {
        "now": hist[-1] if hist else stats.sys_sample(),
        "history": hist,
        "cores": snap.get("cores", 0),
        "uptime": uptime,
        "streams": len(paths),
        "viewing": sum(1 for sp in paths.values() if readers.get(sp, 0) > 0),
        "viewers": sum(readers.get(sp, 0) for sp in paths.values()),
    }


@router.get("/warm")
async def warm_status(db: Db, _: Admin) -> dict:
    """Warm-keeper holati: tizim CPU bandligi, "issiq" (bir zumda ochiladigan)
    kameralar soni va ro'yxati. Har kamera uchun ayni vaqtdagi tomoshabin soni
    ham ko'rsatiladi (viewers > 0 = hozir ko'rilmoqda)."""
    from vizora.models.ezviz_account import EzvizAccount

    snap = ezviz_manager.warm_snapshot()
    readers = await ezviz_manager._path_readers()
    stream_paths: dict[str, str] = snap.get("stream_paths", {})
    warm_ids = list(stream_paths.keys())

    # Issiq kameralar nomi + provayderi (hik/ezviz). Provayder LEFT JOIN orqali.
    info: dict[str, dict] = {}
    if warm_ids:
        ids = [uuid.UUID(x) for x in warm_ids]
        rows = (
            await db.execute(
                select(Camera.id, Camera.name, EzvizAccount.provider)
                .outerjoin(EzvizAccount, EzvizAccount.id == Camera.ezviz_account_id)
                .where(Camera.id.in_(ids))
            )
        ).all()
        info = {str(cid): {"name": name, "provider": prov} for cid, name, prov in rows}

    # Jami HIK kamera (warm-keeper shularni navbat bilan qiziydi).
    hik_total = (
        await db.execute(
            select(func.count(Camera.id))
            .join(EzvizAccount, EzvizAccount.id == Camera.ezviz_account_id)
            .where(
                Camera.enabled.is_(True),
                Camera.source_type == "ezviz",
                EzvizAccount.provider == "hik",
            )
        )
    ).scalar_one()

    cameras = [
        {
            "id": cid,
            "name": (info.get(cid) or {}).get("name") or cid[:8],
            "provider": (info.get(cid) or {}).get("provider"),
            "viewers": readers.get(sp, 0),
        }
        for cid, sp in stream_paths.items()
    ]
    # Ko'rilayotganlar boshida, keyin nom bo'yicha.
    cameras.sort(key=lambda x: (-x["viewers"], x["name"].lower()))
    return {
        "cpu": snap["cpu"],
        "cores": snap["cores"],
        "budget": snap["budget"],
        "hik_total": hik_total,
        "warm_count": len(cameras),
        "viewing_count": sum(1 for c in cameras if c["viewers"] > 0),
        "cameras": cameras,
    }


@router.get("/faults")
async def fault_status(db: Db, _: Admin) -> dict:
    """Nosoz kameralar — ALOHIDA KATEGORIYA.

    "Oqim yo'q" (`no_stream`): qurilma bulutda online, lekin publisher video
    olmadi — batareya tugagan, uzilgan uplink, noto'g'ri kanal yoki verify-kod.
    Bu holat oldin oddiy "online" ko'rinardi, ya'ni muammo yashiringan edi.
    `sabab` ni ko'rish uchun har biriga publisher logi bor
    (`GET /monitoring/camera-log/{id}`). Ro'yxat xotiradan olinadi
    ([[manager.faults]]) va DB'dagi `status="no_stream"` bilan birlashtiriladi —
    server qayta ishga tushsa DB saqlab qolgani ko'rinadi.
    """
    live = ezviz_manager.faults()
    conds = [Camera.status == "no_stream"]
    if live:
        conds.append(Camera.id.in_([uuid.UUID(c) for c in live]))
    rows = (
        await db.execute(
            select(Camera, Zone, Branch)
            .outerjoin(Zone, Camera.zone_id == Zone.id)
            .outerjoin(Branch, Zone.branch_id == Branch.id)
            .where(or_(*conds))
            .order_by(Camera.name)
        )
    ).all()
    items = []
    for c, f, e in rows:
        info = live.get(str(c.id)) or {}
        items.append({
            "id": str(c.id),
            "name": c.name,
            "status": c.status,
            "reason": info.get("reason") or "no_stream",
            "tries": info.get("tries"),
            "since_s": info.get("since_s"),
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "zone_name": f.name if f else None,
            "branch_name": e.name if e else None,
        })
    return {
        "recheck_seconds": settings.ezviz_fault_recheck_seconds,
        "count": len(items),
        "cameras": items,
    }


@router.get("/logs")
async def system_logs(
    _: Admin,
    level: str | None = None,
    q: str | None = None,
    limit: int = 300,
) -> dict:
    """Tizim loglari (xotira buferidan) — xato/ogohlantirishlarni web'da ko'rish.

    level — DEBUG|INFO|WARNING|ERROR (shu daraja va yuqorisi); q — qidiruv.
    """
    from vizora.core import logbuf

    limit = max(1, min(limit, 1000))
    return {"records": logbuf.get_records(level=level, q=q, limit=limit)}


@router.get("/camera-log/{cid}")
async def camera_log(cid: uuid.UUID, _: AdminOnly, lines: int = 200) -> dict:
    """Bitta kameraning publisher (oqim/ffmpeg) logi — nega ochilmayotganini
    aniqlash uchun. Fayl: {ezviz_token_dir}/pub-<id>.log (oxirgi N qator)."""
    import os

    from vizora.core.config import settings

    lines = max(1, min(lines, 1000))
    path = os.path.join(settings.ezviz_token_dir, f"pub-{cid}.log")
    if not os.path.exists(path):
        return {"exists": False, "lines": []}
    try:
        from vizora.core.logbuf import redact

        with open(path, "rb") as f:
            # Faqat oxirgi qismi — log fayli katta bo'lsa butunini xotiraga o'qimaymiz.
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 512 * 1024))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        tail = [redact(x) for x in text.splitlines()[-lines:]]
        return {"exists": True, "lines": tail}
    except OSError as exc:
        return {"exists": False, "lines": [], "error": str(exc)}


@router.get("/stream")
async def status_stream(request: Request, _: Admin) -> StreamingResponse:
    """SSE: Redis `camera:status` o'zgarishlarini brauzerga oqim qiladi.

    Har hodisa: `data: {"camera_id": "...", "status": "online"|"offline"|"error"}`.
    Har ~15s da `: ping` (heartbeat) — disconnect aniqlash va proksi timeoutidan saqlanish.
    """
    redis = request.app.state.redis

    async def gen():
        pubsub = redis.pubsub()
        await pubsub.subscribe(STATUS_CHANNEL)
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=_HEARTBEAT_SECONDS
                )
                if msg and msg.get("type") == "message":
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode()
                    yield f"data: {data}\n\n"
                else:
                    yield ": ping\n\n"  # heartbeat
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(STATUS_CHANNEL)
                await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx buferlashini o'chirish (SSE darhol ketsin)
        },
    )
