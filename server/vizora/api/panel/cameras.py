"""Kameralar CRUD + filtrlash + bulk import. RTSP URL shifrlangan saqlanadi."""
import csv
import io
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.api.patch import reject_null_columns
from vizora.api.deps import CurrentUser, require_role
from vizora.api.responses import snapshot_response
from vizora.core import crypto, netguard
from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.ezviz_account import EzvizAccount
from vizora.models.zone import Zone
from vizora.schemas.camera import (
    CameraCreate,
    CameraImportResult,
    CameraImportRow,
    CameraOut,
    CameraTestRequest,
    CameraTestResult,
    CameraUpdate,
    EzvizChannel,
    EzvizDevice,
    EzvizDiscoverResult,
    IsupDevice,
    IsupDiscoverResult,
    IsupStream,
    PrewarmRequest,
)
from vizora.media import mediamtx, stream
from vizora.services import camera_streams, probe, snapshot
from vizora.sources import isup
from vizora.sources.ezviz import manager as ezviz_manager
from vizora.sources.ezviz import tokens as ezviz_tokens

router = APIRouter(prefix="/cameras", tags=["cameras"])
log = logging.getLogger("vizora.cameras")

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
Db = Annotated[AsyncSession, Depends(get_db)]

_MAX_IMPORT_ROWS = 500


async def _get_or_404(db: AsyncSession, cid: uuid.UUID) -> Camera:
    obj = await db.get(Camera, cid)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Kamera topilmadi")
    return obj


async def _check_account(db: AsyncSession, aid: uuid.UUID) -> EzvizAccount:
    acc = await db.get(EzvizAccount, aid)
    if acc is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "EZVIZ hisobi mavjud emas")
    return acc


async def _isup_device(db: AsyncSession, cam: Camera, key_choice: str | None, user) -> None:
    """ISUP kamera qurilmasini reestrga yozadi va (admin bo'lsa) kalitini o'rnatadi."""
    if key_choice is not None and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ISUP kalitini faqat admin tanlaydi")
    try:
        await isup.ensure_device(db, cam.isup_device_id, cam.zone_id, key_choice)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


async def _check_isup_unique(db: AsyncSession, dev: str, ch: int,
                             exclude: uuid.UUID | None = None) -> None:
    """Bitta (Device ID, kanal) — bitta kamera: ikkinchisi bir xil oqimni ko'rsatardi."""
    stmt = select(Camera.id).where(
        Camera.source_type == "isup", Camera.isup_device_id == dev, Camera.isup_channel == ch,
    )
    if exclude is not None:
        stmt = stmt.where(Camera.id != exclude)
    if (await db.execute(stmt.limit(1))).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"ISUP {dev} kanal {ch} allaqachon boshqa kameraga biriktirilgan")


async def _guard_rtsp(*urls: str | None) -> None:
    """Operator bergan RTSP URL ichki xizmatga ishora qilmasin ([[netguard]])."""
    for u in urls:
        reason = await netguard.internal_reason(u)
        if reason:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"RTSP URL rad etildi: {reason}")


async def _check_zone(db: AsyncSession, fid: uuid.UUID) -> Zone:
    f = await db.get(Zone, fid)
    if f is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hudud mavjud emas")
    return f


def _filtered(stmt, branch_id, zone_id, source_type, q, ezviz_account_id=None):
    """Ro'yxat va hisoblagich uchun UMUMIY filtrlar (holatdan tashqari)."""
    if ezviz_account_id is not None:
        stmt = stmt.where(Camera.ezviz_account_id == ezviz_account_id)
    if zone_id is not None:
        stmt = stmt.where(Camera.zone_id == zone_id)
    if branch_id is not None:
        stmt = stmt.join(Zone, Zone.id == Camera.zone_id).where(Zone.branch_id == branch_id)
    if source_type is not None:
        stmt = stmt.where(Camera.source_type == source_type)
    if q:
        stmt = stmt.where(Camera.name.ilike(f"%{q}%"))
    return stmt


# Saralash: "new" (default, yangi qo'shilgan boshida), "name", "status" (muammolilar
# tepada: oqim yo'q → xatolik → oflayn → onlayn), "seen" (yaqinda ko'rilgan boshida).
_STATUS_RANK = case(
    (Camera.status == "no_stream", 0), (Camera.status == "error", 1),
    (Camera.status == "offline", 2), else_=3,
)
_SORTS = {
    "new": (Camera.created_at.desc(), Camera.name),
    "name": (func.lower(Camera.name), Camera.created_at.desc()),
    "status": (_STATUS_RANK, func.lower(Camera.name)),
    "seen": (Camera.last_seen_at.desc().nulls_last(), func.lower(Camera.name)),
}


@router.get("", response_model=list[CameraOut])
async def list_cameras(
    db: Db,
    _: Admin,
    response: Response,
    branch_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    source_type: str | None = None,
    q: str | None = Query(default=None, description="nom bo'yicha qidiruv"),
    status_: str | None = Query(default=None, alias="status"),
    ezviz_account_id: uuid.UUID | None = None,
    sort: str = Query(default="new", description="new | name | status | seen"),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[Camera]:
    """Filial/hudud/manba/holat/nom bo'yicha filtr + saralash + pagination.

    `limit` berilmasa hammasi qaytadi (orqaga moslik). Umumiy soni
    `X-Total-Count` header'ida. `status=problem` — oqim yo'q + xatolik.
    """
    stmt = _filtered(select(Camera), branch_id, zone_id, source_type, q, ezviz_account_id)
    if status_ == "problem":
        stmt = stmt.where(Camera.status.in_(("no_stream", "error")))
    elif status_ is not None:
        stmt = stmt.where(Camera.status == status_)

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    stmt = stmt.order_by(*_SORTS.get(sort, _SORTS["new"])).offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


@router.get("/counts")
async def camera_counts(
    db: Db,
    _: Admin,
    branch_id: uuid.UUID | None = None,
    zone_id: uuid.UUID | None = None,
    source_type: str | None = None,
    q: str | None = None,
    ezviz_account_id: uuid.UUID | None = None,
) -> dict:
    """Joriy filtrlar bo'yicha holatlar soni — Kameralar sahifasidagi holat tablari uchun.

    Global `/dashboard/summary` emas: filial/hudud/qidiruv tanlanganda tablar aynan
    ko'rinib turgan to'plamni sanashi kerak."""
    stmt = _filtered(select(Camera.status, func.count()).select_from(Camera),
                     branch_id, zone_id, source_type, q, ezviz_account_id).group_by(Camera.status)
    by = dict((await db.execute(stmt)).all())
    return {
        "all": sum(by.values()),
        "online": by.get("online", 0),
        "offline": by.get("offline", 0),
        "problem": by.get("no_stream", 0) + by.get("error", 0),
    }


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(payload: CameraCreate, db: Db, user: Admin) -> Camera:
    if payload.source_type == "ezviz":
        await _check_account(db, payload.ezviz_account_id)
    if payload.source_type == "isup":
        await _check_isup_unique(db, payload.isup_device_id, payload.isup_channel)
    if payload.zone_id is not None:
        await _check_zone(db, payload.zone_id)
    if payload.source_type == "rtsp":
        await _guard_rtsp(payload.rtsp_url, payload.rtsp_sub_url)
    cam_id = uuid.uuid4()
    obj = Camera(
        id=cam_id,
        zone_id=payload.zone_id,
        name=payload.name,
        source_type=payload.source_type,
        rtsp_url_enc=crypto.encrypt(payload.rtsp_url) if payload.rtsp_url else None,
        rtsp_sub_url_enc=crypto.encrypt(payload.rtsp_sub_url) if payload.rtsp_sub_url else None,
        ezviz_account_id=payload.ezviz_account_id if payload.source_type == "ezviz" else None,
        ezviz_serial=payload.ezviz_serial,
        ezviz_channel=payload.ezviz_channel or 1,
        ezviz_verify_code_enc=(
            crypto.encrypt(payload.ezviz_verify_code) if payload.ezviz_verify_code else None
        ),
        codec=payload.codec,
        transcode=payload.transcode,
        enabled=payload.enabled,
        keep_warm=payload.keep_warm,
        stream_path=f"cam/{cam_id}",
        status="offline",
    )
    if payload.source_type == "isup":
        obj.isup_device_id = payload.isup_device_id
        obj.isup_channel = payload.isup_channel
        isup.fill_urls(obj)
        await _isup_device(db, obj, payload.isup_key, user)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    await camera_streams.apply_changes(obj, created=True)
    return obj


@router.get("/ezviz/devices", response_model=EzvizDiscoverResult)
async def discover_ezviz_devices(
    account_id: uuid.UUID, db: Db, _: Admin
) -> EzvizDiscoverResult:
    """EZVIZ hisobidagi kameralarni aniqlaydi (qo'shish uchun ro'yxat)."""
    acc = await db.get(EzvizAccount, account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "EZVIZ hisobi topilmadi")
    provider = acc.provider or "ezviz"
    region = acc.region or (
        settings.hik_default_region if provider == "hik" else settings.ezviz_default_region
    )
    password = crypto.decrypt(acc.password_enc)
    try:
        devices = await ezviz_tokens.get_devices(
            str(acc.id), acc.account, password, region, provider,
        )
        # NVR kanallari (VTM resurslaridan) — ko'p kanalli qurilmalar uchun.
        channels = await ezviz_tokens.get_channels(
            str(acc.id), acc.account, password, region, provider,
        )
    except Exception as exc:  # noqa: BLE001
        # Xom upstream xato matnini (token/ichki ma'lumot) mijozga bermaymiz —
        # umumiy xabar, batafsili faqat server logida.
        log.warning("EZVIZ discovery xatosi (hisob %s): %s", account_id, exc)
        return EzvizDiscoverResult(ok=False, error="EZVIZ bulutiga ulanib bo'lmadi")

    # Shu hisobda allaqachon qo'shilgan (serial, kanal) juftliklari
    existing_rows = (
        await db.execute(
            select(Camera.ezviz_serial, Camera.ezviz_channel).where(
                Camera.ezviz_account_id == account_id,
                Camera.ezviz_serial.is_not(None),
            )
        )
    ).all()
    existing = {s for s, _ in existing_rows}                       # serial bo'yicha (qurilma)
    existing_ch = {(s, c or 1) for s, c in existing_rows}          # (serial, kanal) bo'yicha
    items = [
        EzvizDevice(
            serial=serial,
            name=d["name"],
            online=d["status"] == 1,
            model=d["model"],
            channels=d["channels"],
            already_added=serial in existing,
            encrypted=d.get("encrypted", False),
        )
        for serial, d in devices.items()
    ]
    items.sort(key=lambda x: (x.already_added, not x.online, x.name.lower()))
    # Shifrlanganlik qurilma bo'yicha — kanallarni serial orqali belgilaymiz.
    enc_by_serial = {s: d.get("encrypted", False) for s, d in devices.items()}
    chan_items = [
        EzvizChannel(
            serial=c["serial"],
            channel=c["channel"],
            name=c["name"],
            already_added=(c["serial"], c["channel"]) in existing_ch,
            encrypted=enc_by_serial.get(c["serial"], False),
        )
        for c in channels
    ]
    return EzvizDiscoverResult(ok=True, devices=items, channels=chan_items)


@router.get("/isup/devices", response_model=IsupDiscoverResult)
async def discover_isup_devices(db: Db, _: Admin) -> IsupDiscoverResult:
    """Hozir serverga ISUP orqali ulangan qurilmalar (isup-bridge'dan).

    Qurilma panelda qo'shilmasdan ham ulanadi — shuning uchun yangi kamerani
    qo'shishda Device ID'ni qo'lda yozish shart emas, ro'yxatdan tanlanadi."""
    out = IsupDiscoverResult(ok=False, server_host=settings.isup_public_host,
                             server_port=settings.isup_cms_port)
    if not isup.enabled():
        out.error = "ISUP o'chiq (ISUP_BRIDGE_URL sozlanmagan)"
        return out
    state = await isup.bridge_state(max_age=0)
    if state is None:
        out.error = "isup-bridge javob bermayapti — konteyner ishga tushganini tekshiring"
        return out
    rows = (
        await db.execute(
            select(Camera.isup_device_id, Camera.isup_channel).where(Camera.source_type == "isup")
        )
    ).all()
    added: dict[str, list[int]] = {}
    for dev, ch in rows:
        added.setdefault(dev, []).append(ch or 1)
    out.devices = sorted(
        (
            IsupDevice(
                device_id=dev,
                valid=bool(d.get("valid")),
                serial=d.get("serial") or "",
                model=d.get("full_serial") or "",
                ip=d.get("ip") or "",
                firmware=d.get("firmware") or "",
                online_seconds=int(d.get("online_seconds") or 0),
                streams=[
                    IsupStream(channel=s.get("channel", 1), sub=s.get("type") == isup.SUB,
                               live=bool(s.get("live")))
                    for s in d.get("streams") or []
                ],
                added_channels=sorted(added.get(dev, [])),
            )
            for dev, d in state.items()
        ),
        key=lambda x: (bool(x.added_channels), x.device_id.lower()),
    )
    out.ok = True
    return out


@router.post("/prewarm")
async def prewarm_cameras(payload: PrewarmRequest, db: Db, _: Admin) -> dict:
    """Hik kameralarni oldindan "issiq" tutadi — kamera sahifasi ochilganda
    chaqiriladi, shunda ko'rish (Ko'rish) bir zumda ochiladi va bulutga
    ochish/yopish churn'i kamayadi.

    FAQAT Hik (simli NVR) kameralar — batareyali EZVIZ tegilmaydi (batareya
    tejash). Bloklamaydi (wait_ready yo'q) va idempotent: allaqachon issiq bo'lsa
    faqat "soat" yangilanadi. Integratsiya/iste'molchi API'siga ta'sir qilmaydi —
    bu shunchaki oldindan tayyorlash.
    """
    # Oldindan isitish o'chirilgan bo'lsa (hik_keep_warm=False) — hech narsa qilmaymiz.
    # Kameralar faqat so'ralganda (Ko'rish bosilganda) on-demand ishga tushadi.
    if not settings.hik_keep_warm or not payload.ids:
        return {"warmed": 0}
    rows = (
        await db.execute(
            select(Camera, EzvizAccount.provider)
            .join(EzvizAccount, EzvizAccount.id == Camera.ezviz_account_id)
            .where(
                Camera.id.in_(payload.ids),
                Camera.enabled.is_(True),
                Camera.source_type == "ezviz",
            )
        )
    ).all()
    warmed = 0
    for cam, provider in rows:
        if (provider or "ezviz") != "hik":
            continue  # batareyali EZVIZ'ni issiq tutmaymiz
        try:
            await mediamtx.safe_ensure_path(cam.stream_path)
            # Oldindan isitish PANEL uchun — XOM rejim yetarli (enkodersiz, CPU ~0).
            # HLS/iste'molchi kelsa publisher o'zi to'liq rejimga o'tadi.
            await ezviz_manager.ensure_for_camera(db, cam, need_h264=False)
            warmed += 1
        except ezviz_manager.EzvizConfigError:
            pass  # sozlanmagan kamera — jimgina o'tkazamiz
    return {"warmed": warmed}


@router.post("/{cid}/prewarm")
async def prewarm_camera(cid: uuid.UUID, db: Db, _: Admin) -> dict:
    """BITTA kamerani foydalanuvchi NIYATI bo'yicha oldindan tayyorlaydi.

    Grid'da kartaga sichqoncha kelganda chaqiriladi: bosishgacha ~1-2s oldin
    publisher boshlanadi va bulut bilan qo'l siqish (o'lchandi: batareyali EZVIZ
    kamerada `stream.start()` 4.6s, VTM 2.4s) BOSISHDAN OLDIN bajariladi. Aks holda
    o'sha vaqt "Ulanmoqda…" bo'lib ko'rinadi.

    Ommaviy `POST /prewarm` dan farqi: bu YAKKA kamera, HAR QANDAY provayder
    (batareyali EZVIZ ham — foydalanuvchi aynan shuni ko'rmoqchi) va
    `hik_keep_warm` bayrog'iga bog'liq EMAS (u fon siyosati uchun, bu esa
    foydalanuvchi harakati). Bloklamaydi — `wait_ready` yo'q; idempotent.
    Frontend'da debounce + kamera bo'yicha memo bor, shuning uchun grid bo'ylab
    sichqoncha yugurtirish o'nlab publisher ochmaydi."""
    cam = await _get_or_404(db, cid)
    if not cam.enabled:
        return {"warmed": False, "reason": "kamera o'chirilgan"}
    try:
        await camera_streams.prewarm(db, cam)
        return {"warmed": True}
    except camera_streams.StreamUnavailable as exc:
        return {"warmed": False, "reason": str(exc)}


def _csv_safe(v) -> str:
    """CSV formula in'ektsiyasiga qarshi: `= + - @`, tab yoki CR bilan boshlangan qiymat
    Excel'da formula bo'lib ishlaydi. Operator kamera nomini `=HYPERLINK(...)` qilsa,
    admin eksportni ochganda ichidagi parollar begona serverga ketardi — oldiga `'`."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/export")
async def export_cameras(
    db: Db,
    user: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> Response:
    """Barcha kameralarni CSV (Excel) ga eksport — PAROLLAR bilan (deshifrlangan).

    FAQAT admin. Maxfiy ma'lumot (RTSP parol, EZVIZ hisob paroli, verify code)
    deshifrlanadi. Audit uchun loglanadi. CSV UTF-8 BOM bilan — Excel to'g'ri ochadi.
    """


    log.warning("Kameralar PAROL bilan eksport qilindi (admin=%s)", getattr(user, "username", "?"))

    rows = (
        await db.execute(
            select(Camera, Zone, Branch, EzvizAccount)
            .outerjoin(Zone, Camera.zone_id == Zone.id)
            .outerjoin(Branch, Zone.branch_id == Branch.id)
            .outerjoin(EzvizAccount, Camera.ezviz_account_id == EzvizAccount.id)
            .order_by(Camera.created_at.desc())
        )
    ).all()

    def dec(blob: bytes | None) -> str:
        if not blob:
            return ""
        try:
            return crypto.decrypt(blob)
        except Exception:  # noqa: BLE001 — kalit muammosi bo'lsa ham eksport buzilmasin
            return "(deshifrlab bo'lmadi)"

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM — Excel kirill/o'zbek matnini to'g'ri ko'rsatadi
    # Ajratuvchi ';' — Excel (rus/yevropa tili) aynan shuni ustunlarga ajratadi
    # (vergul bilan hammasi bitta ustunga tushib qolardi).
    w = csv.writer(buf, delimiter=";")
    w.writerow([
        "Nomi", "Manba", "Holat", "Yoqilgan", "Filial", "Hudud",
        "RTSP URL", "RTSP sub URL",
        "EZVIZ hisob", "EZVIZ login", "EZVIZ parol", "Region", "Provider",
        "Serial", "Kanal", "Verify code",
        "Kodek", "Transcode", "Substream", "Stream path", "Yaratilgan",
    ])
    for cam, fac, ent, acc in rows:
        w.writerow([_csv_safe(x) for x in [
            cam.name, cam.source_type, cam.status, "ha" if cam.enabled else "yo'q",
            ent.name if ent else "", fac.name if fac else "",
            dec(cam.rtsp_url_enc), dec(cam.rtsp_sub_url_enc),
            acc.label if acc else "", acc.account if acc else "",
            dec(acc.password_enc) if acc else "", (acc.region if acc else "") or "",
            acc.provider if acc else "",
            cam.ezviz_serial or cam.isup_device_id or "",
            cam.ezviz_channel if cam.is_ezviz else cam.isup_channel if cam.is_isup else "",
            dec(cam.ezviz_verify_code_enc),
            cam.codec, "ha" if cam.transcode else "yo'q",
            "ha" if cam.ezviz_use_substream else "yo'q",
            cam.stream_path, cam.created_at.isoformat() if cam.created_at else "",
        ]])
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kameralar.csv"'},
    )


@router.get("/{cid}", response_model=CameraOut)
async def get_camera(cid: uuid.UUID, db: Db, _: Admin) -> Camera:
    return await _get_or_404(db, cid)


@router.get("/{cid}/stream")
async def get_camera_stream(cid: uuid.UUID, db: Db, user: Admin,
                            transport: str = Query(default="hls"),
                            hevc: bool = Query(default=False),
                            sub: bool = Query(default=False)) -> dict:
    """Admin panel uchun: token bilan oqim URL'lari (Player shu orqali ko'radi).

    `transport=webrtc|ws` — pleyer WebRTC (WHEP) yoki WebSocket+WebCodecs bilan
    ochmoqchi: HLS muxerini isitish va segment kutish O'TKAZIB YUBORILADI (ularga
    segment kerak emas), shuning uchun javob ~2s tezroq qaytadi. `hls_url` baribir
    qaytariladi — tez yo'l ulanmasa pleyer shunga qaytadi."""
    cam = await _get_or_404(db, cid)
    # Xom `<path>/raw` faqat tez ochilsa (hub keshida I-kadr / xom publisher ishlayapti /
    # devor plitkasi); aks holda H.264 — enkoder har 1s da I-kadr qo'yadi.
    raw = await camera_streams.choose_raw(cam, transport=transport, hevc=hevc, substream=sub)
    if cam.enabled:
        try:
            # `sub=1` — devor plitkasi: KICHIK oqim. 3200x1800 plitkani ekrandagi
            # ~250px uchun dekodlash brauzerni yiqitadi (23 plitkada buzilish + qotish).
            await camera_streams.start(db, cam, transport=transport,
                                       need_h264=not raw, substream=sub)
        except camera_streams.StreamUnavailable as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    out = camera_streams.viewer_links(cam, stream.owner_user(user.username, user.token_version))
    out |= camera_streams.ws_links(cam, out["token"], raw=raw, substream=sub)
    return out


@router.post("/snapshot-cache/clear")
async def clear_snapshot_cache(_: Admin) -> dict:
    """Keshdagi eski snapshot'larni tozalaydi — keyingi so'rovda 0'dan olinadi
    (qizigan kameralar jonli-grab beradi). Restart kerak emas."""
    return {"cleared": snapshot.clear()}


@router.get("/{cid}/snapshot")
async def get_camera_snapshot(cid: uuid.UUID, db: Db, _: Admin,
                              fresh: bool = Query(default=False)) -> Response:
    """Grid uchun arzon "ko'zni aldash": kameraning oxirgi keshlangan JPEG rasmi.

    Jonli transkod o'rniga — keshdagi rasm DARROV qaytadi (CPU ~0). Kesh eskirgan
    bo'lsa FONDA yangi rasm olinadi (bloklamaydi). Hali rasm yo'q bo'lsa 202 (mijoz
    biroz keyin qayta so'raydi). Jonli oqim pipeline'iga tegmaydi."""
    # Snapshot o'chirilgan bo'lsa — darrov 204 (DB ishlatmasdan). Mijoz buni ko'rib
    # qayta so'rashni to'xtatadi (behuda poll yo'q).
    if not settings.ezviz_snapshot_enabled:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    cam = await _get_or_404(db, cid)
    # Iste'molchi API'si ham AYNI shu yordamchini chaqiradi — mantiq bir joyda.
    return await snapshot_response(db, cam, fresh=fresh)


@router.post("/test", response_model=CameraTestResult)
async def test_rtsp(payload: CameraTestRequest, _: Admin) -> CameraTestResult:
    """Kamera qo'shishdan oldin RTSP URL'ni ffprobe bilan sinash."""
    await _guard_rtsp(payload.rtsp_url)
    info = await probe.probe_info(payload.rtsp_url, timeout=12.0)
    if info is None:
        return CameraTestResult(ok=False)
    return CameraTestResult(ok=True, codec=info["codec"], width=info["width"], height=info["height"])


@router.post("/{cid}/test", response_model=CameraTestResult)
async def test_camera(cid: uuid.UUID, db: Db, _: Admin) -> CameraTestResult:
    """Mavjud kamera RTSP'sini sinash (faqat RTSP kameralar uchun)."""
    cam = await _get_or_404(db, cid)
    if cam.is_ezviz or not cam.rtsp_url_enc:
        # EZVIZ kamerada RTSP yo'q — decrypt(None) 500 bermasin.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bu kamerada sinaladigan RTSP yo'q")
    url = crypto.decrypt(cam.rtsp_url_enc)
    if cam.source_type == "rtsp":   # ISUP URL'i backendning o'zi qo'ygan mediamtx manzili
        await _guard_rtsp(url)
    info = await probe.probe_info(url, timeout=12.0)
    if info is None:
        return CameraTestResult(ok=False)
    return CameraTestResult(ok=True, codec=info["codec"], width=info["width"], height=info["height"])


@router.patch("/{cid}", response_model=CameraOut)
async def update_camera(
    cid: uuid.UUID, payload: CameraUpdate, db: Db, user: Admin
) -> Camera:
    obj = await _get_or_404(db, cid)
    data = payload.model_dump(exclude_unset=True)
    reject_null_columns(obj, data)
    isup_key = data.pop("isup_key", None)
    old_isup = (obj.source_type, obj.isup_device_id, obj.isup_channel)
    # ISUP kamerada saqlangan URL `isup/...` path'iga ishora qiladi — RTSP'ga
    # o'tishda u "mavjud URL" bo'lib tekshiruvdan o'tib ketmasin.
    if obj.is_isup and data.get("source_type") == "rtsp" and not data.get("rtsp_url"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "RTSP kamera uchun rtsp_url majburiy")
    if data.get("ezviz_account_id"):
        await _check_account(db, data["ezviz_account_id"])
    if data.get("zone_id"):
        await _check_zone(db, data["zone_id"])
    if data.get("source_type", obj.source_type) == "rtsp":
        await _guard_rtsp(data.get("rtsp_url"), data.get("rtsp_sub_url"))
    if "rtsp_url" in data:
        rtsp = data.pop("rtsp_url")
        obj.rtsp_url_enc = crypto.encrypt(rtsp) if rtsp else None
    if "rtsp_sub_url" in data:
        sub = data.pop("rtsp_sub_url")
        obj.rtsp_sub_url_enc = crypto.encrypt(sub) if sub else None
    if "ezviz_verify_code" in data:
        code = data.pop("ezviz_verify_code")
        obj.ezviz_verify_code_enc = crypto.encrypt(code) if code else None
    for k, v in data.items():
        setattr(obj, k, v)
    # Birlashgan holatni tekshiramiz — source_type o'zgarsa kamera yarim
    # sozlangan qolib ketmasin (RTSP'siz rtsp kamera / serialsiz ezviz).
    if obj.source_type == "rtsp" and not obj.rtsp_url_enc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "RTSP kamera uchun rtsp_url majburiy")
    if obj.source_type == "ezviz" and not (obj.ezviz_serial and obj.ezviz_account_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "EZVIZ kamera uchun ezviz_serial va ezviz_account_id majburiy",
        )
    if obj.is_isup:
        if not obj.isup_device_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ISUP kamera uchun isup_device_id majburiy")
        await _check_isup_unique(db, obj.isup_device_id, obj.isup_channel or 1, exclude=obj.id)
        if (obj.source_type, obj.isup_device_id, obj.isup_channel) != old_isup:
            # Boshqa qurilma/kanal — kodek qaytadan aniqlanadi (probe [[isup.want_lines]]).
            obj.codec, obj.transcode, obj.sub_codec, obj.sub_transcode = "auto", False, "auto", False
        isup.fill_urls(obj)
        await _isup_device(db, obj, isup_key, user)
    await db.commit()
    await db.refresh(obj)
    # rtsp/transcode/enabled/EZVIZ sozlamalari o'zgargan bo'lishi mumkin
    await camera_streams.apply_changes(obj)
    return obj


@router.delete("/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(cid: uuid.UUID, db: Db, _: Admin) -> None:
    obj = await _get_or_404(db, cid)
    stream_path, is_ezviz = obj.stream_path, obj.is_ezviz
    await db.delete(obj)
    await db.commit()
    await camera_streams.teardown(str(cid), stream_path, is_ezviz=is_ezviz)


@router.post("/import", response_model=CameraImportResult)
async def import_cameras(
    rows: list[CameraImportRow], db: Db, _: Admin
) -> CameraImportResult:
    """Bulk import — RTSP kameralarni qo'shadi (ixtiyoriy hudud kodi bilan)."""
    if len(rows) > _MAX_IMPORT_ROWS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Bir martada eng ko'pi {_MAX_IMPORT_ROWS} qator import qilinadi",
        )
    # zone_code -> id keshi (ixtiyoriy biriktirish uchun)
    zones = {f.code: f.id for f in (await db.execute(select(Zone))).scalars().all()}
    created, skipped, errors = 0, 0, []
    new_cams: list[Camera] = []
    for i, row in enumerate(rows):
        if not row.rtsp_url:
            skipped += 1
            errors.append(f"#{i}: rtsp_url bo'sh")
            continue
        reason = (await netguard.internal_reason(row.rtsp_url)
                  or await netguard.internal_reason(row.rtsp_sub_url))
        if reason:
            skipped += 1
            errors.append(f"#{i}: {reason}")
            continue
        fid = zones.get(row.zone_code) if row.zone_code else None
        if row.zone_code and fid is None:
            skipped += 1
            errors.append(f"#{i}: hudud kodi topilmadi: {row.zone_code}")
            continue
        # Bitta qator xatosi (masalan shifrlash) butun batchni 500 qilmasin.
        try:
            cam_id = uuid.uuid4()
            cam = Camera(
                id=cam_id,
                zone_id=fid,
                name=row.name,
                rtsp_url_enc=crypto.encrypt(row.rtsp_url),
                rtsp_sub_url_enc=crypto.encrypt(row.rtsp_sub_url) if row.rtsp_sub_url else None,
                codec=row.codec,
                transcode=row.transcode,
                stream_path=f"cam/{cam_id}",
                status="offline",
            )
            db.add(cam)
            new_cams.append(cam)
            created += 1
        except Exception as exc:  # noqa: BLE001
            skipped += 1
            errors.append(f"#{i}: qo'shib bo'lmadi")
            log.warning("Import qator #%d xatosi: %s", i, exc)
    await db.commit()
    for cam in new_cams:
        await camera_streams.configure(cam)
    return CameraImportResult(created=created, skipped=skipped, errors=errors)
