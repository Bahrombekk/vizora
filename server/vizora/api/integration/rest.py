"""Iste'molchi REST API (v1) — tashqi tizimlar API kalit bilan kameralarni
topadi, ko'radi va oqim (HLS) URL'ini oladi.

WebSocket (`/ws/consumer`) bilan bir xil ruxsat (scope) qoidalari, lekin oddiy
REST: yangi tizim kameralarni `GET /cameras` orqali ro'yxatga oladi, o'ziga
tanlab qo'shadi va `GET /cameras/{id}/stream` orqali jonli oqimni ochadi.

Autentifikatsiya (quyidagilardan biri):
  - Header:  X-API-Key: cs_...
  - Header:  Authorization: Bearer cs_...
  - Query:   ?api_key=cs_...      (faqat brauzer/qulaylik uchun)
"""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.api.deps import get_api_client
from vizora.api.responses import snapshot_response
from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.models.camera import Camera
from vizora.models.client import ApiClient
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.media import stream
from vizora.services import camera_streams, consumer_quota
from vizora.services.access import can_access

router = APIRouter(prefix="/v1", tags=["consumer-api"])

Client = Annotated[ApiClient, Depends(get_api_client)]
Db = Annotated[AsyncSession, Depends(get_db)]


def _cam_dict(cam: Camera, fac: Zone | None, ent: Branch | None) -> dict:
    """Iste'molchiga ko'rsatiladigan xavfsiz kamera ma'lumoti (RTSP yo'q).

    Filial/hudud ixtiyoriy — biriktirilmagan bo'lsa null qaytadi.
    """
    return {
        "id": str(cam.id),
        "name": cam.name,
        "status": cam.status,
        "codec": cam.codec,
        "transcode": cam.transcode,
        "last_seen_at": cam.last_seen_at.isoformat() if cam.last_seen_at else None,
        "zone_id": str(fac.id) if fac else None,
        "zone_name": fac.name if fac else None,
        "zone_code": fac.code if fac else None,
        "branch_id": str(ent.id) if ent else None,
        "branch_name": ent.name if ent else None,
    }


async def _accessible(
    db: AsyncSession,
    client: ApiClient,
    *,
    branch_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    status_: str | None = None,
    q: str | None = None,
) -> list[dict]:
    """Mijoz ruxsatiga tushadigan, faol kameralar (filial/hudud ixtiyoriy)."""
    stmt = (
        select(Camera, Zone, Branch)
        .outerjoin(Zone, Camera.zone_id == Zone.id)
        .outerjoin(Branch, Zone.branch_id == Branch.id)
        .where(Camera.enabled.is_(True))
    )
    if zone_id is not None:
        stmt = stmt.where(Camera.zone_id == zone_id)
    if branch_id is not None:
        stmt = stmt.where(Zone.branch_id == branch_id)
    if status_:
        stmt = stmt.where(Camera.status == status_)
    if q:
        stmt = stmt.where(Camera.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Branch.name.nulls_last(), Zone.name.nulls_last(), Camera.name)

    rows = (await db.execute(stmt)).all()
    return [_cam_dict(c, f, e) for c, f, e in rows if can_access(client, c, f)]


async def _load(db: AsyncSession, cid: uuid.UUID, client: ApiClient) -> Camera:
    cam = await db.get(Camera, cid)
    # O'chirilgan kamera iste'molchi uchun YO'Q (ro'yxat ham yashiradi, WS ham
    # bloklaydi) — id'ni biladigan mijoz oqim ochib qo'ymasin.
    if cam is None or not cam.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kamera topilmadi")
    fac = await db.get(Zone, cam.zone_id) if cam.zone_id else None
    if not can_access(client, cam, fac):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu kameraga ruxsat yo'q")
    return cam


@router.get("/me")
async def whoami(client: Client, db: Db) -> dict:
    """Mijoz haqida: nomi, scope, ko'ra oladigan kameralar soni."""
    cams = await _accessible(db, client)
    sc = client.scopes or {}
    return {
        "name": client.name,
        "scopes": sc,
        "all_access": bool(sc.get("all")),
        "camera_count": len(cams),
    }


@router.get("/cameras")
async def list_cameras(
    client: Client,
    db: Db,
    branch_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    status_: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, description="nom bo'yicha qidiruv"),
) -> list[dict]:
    """Mijoz ko'ra oladigan kameralar (filtrlar ixtiyoriy)."""
    return await _accessible(
        db, client, branch_id=branch_id, zone_id=zone_id, status_=status_, q=q
    )


@router.get("/tree")
async def tree(client: Client, db: Db) -> list[dict]:
    """Filial → hudud → kamera daraxti (faqat ruxsat etilganlari).

    Filial/hududga biriktirilmagan kameralar "Biriktirilmagan" ostida.
    """
    items = await _accessible(db, client)
    UNASSIGNED = "Biriktirilmagan"
    ents: dict[str, dict] = {}
    for d in items:
        eid = d["branch_id"] or "__none__"
        ent = ents.setdefault(eid, {"id": d["branch_id"], "name": d["branch_name"] or UNASSIGNED, "zones": {}})
        fid = d["zone_id"] or "__none__"
        fac = ent["zones"].setdefault(
            fid,
            {"id": d["zone_id"], "name": d["zone_name"] or UNASSIGNED, "code": d["zone_code"], "cameras": []},
        )
        fac["cameras"].append(d)
    return [
        {"id": e["id"], "name": e["name"], "zones": list(e["zones"].values())}
        for e in ents.values()
    ]


@router.get("/cameras/{cid}")
async def get_camera(cid: uuid.UUID, client: Client, db: Db) -> dict:
    cam = await _load(db, cid, client)
    fac = await db.get(Zone, cam.zone_id) if cam.zone_id else None
    ent = await db.get(Branch, fac.branch_id) if fac else None
    return _cam_dict(cam, fac, ent)


@router.get("/cameras/{cid}/stream")
async def get_stream(cid: uuid.UUID, client: Client, db: Db, request: Request,
                     transport: str = Query(default="hls")) -> dict:
    """Jonli oqim uchun tokenli URL. Oqim on-demand ishga tushadi.

    `transport=webrtc` — mijoz WebRTC (WHEP) bilan ochmoqchi: HLS muxerini isitish
    va segment kutish O'TKAZIB YUBORILADI (WebRTC'ga segment kerak emas), shuning
    uchun javob sezilarli tezroq qaytadi (o'lchandi: 4.97s -> 1.85s). `hls_url`
    baribir qaytadi — WebRTC ulanmasa mijoz shunga qaytishi mumkin.
    """
    cam = await _load(db, cid, client)
    if not await consumer_quota.allow_camera(getattr(request.app.state, "redis", None),
                                             client.id, cam.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Juda ko'p turli kamera ochildi — birozdan keyin urinib ko'ring")
    # Iste'molchilar H.264 (HLS/WebRTC) o'qiydi — panelning WS/xom yo'li ularga yo'q.
    transport = "webrtc" if transport == "webrtc" else "hls"
    try:
        await camera_streams.start(db, cam, transport=transport)
    except camera_streams.StreamUnavailable as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    # whep_url (agar yoqilgan bo'lsa) HLS bilan AYNI tokenni ishlatadi.
    return {"camera_id": str(cam.id), "name": cam.name,
            **camera_streams.viewer_links(cam, stream.owner_client(client))}


@router.get("/cameras/{cid}/snapshot")
async def get_snapshot(cid: uuid.UUID, client: Client, db: Db,
                       fresh: bool = Query(default=False)) -> Response:
    """Kameraning oxirgi keshlangan JPEG kadri — ro'yxat/grid uchun "poster".

    Nega kerak: jonli oqim ochilishi bulut tomonidan bir necha soniya oladi.
    Shu vaqt ichida qora to'rtburchak o'rniga oxirgi kadrni ko'rsatish mumkin
    (CPU ~0 — kesh diskda, qayta ishga tushirishdan keyin ham saqlanadi).

    Javoblar: **200** + `image/jpeg` · **202** rasm hali yo'q (biroz keyin qayta
    so'rang) · **204** snapshot server sozlamasida o'chirilgan (qayta so'ramang).
    `?fresh=1` — keshni kutmasdan yangilashni majburlaydi.
    """
    if not settings.ezviz_snapshot_enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    cam = await _load(db, cid, client)      # scope tekshiruvi shu yerda
    return await snapshot_response(db, cam, fresh=fresh)
