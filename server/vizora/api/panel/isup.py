"""ISUP boshqaruvi: kalitlar, hududga biriktirish, qurilmalar reestri.

Kalit hudud darajasida: bitta kalit bir nechta hududga yoki hududning o'ziga; kerak
bo'lsa qurilmaning o'z kaliti ([[vizora.models.isup]]). Har o'zgarishdan keyin kalitlar
darhol isup-bridge'ga yuboriladi — kaliti o'zgargan/bloklangan qurilma eski kalit bilan
ulanib qolmaydi (bridge uni chiqarib yuboradi).

Kalit qaytariladigan shifrda saqlanadi (bridge'ga ochiq matn kerak). Ochiq matnni faqat
admin ko'radi va har ko'rish logga yoziladi.
"""
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.api.patch import reject_null_columns
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.models.isup import IsupDevice, IsupKey
from vizora.schemas.isup import (
    ZoneIsupOut,
    ZoneKeysCreate,
    ZoneIsupUpdate,
    ZoneRef,
    IsupDeviceCreate,
    IsupDeviceOut,
    IsupDeviceUpdate,
    IsupEffectiveKey,
    IsupKeyCreate,
    IsupKeyOut,
    IsupKeyRotate,
    IsupKeySecret,
    IsupKeyUpdate,
    IsupOverview,
    IsupRejected,
)
from vizora.core import crypto
from vizora.sources import isup

router = APIRouter(prefix="/isup", tags=["isup"])
log = logging.getLogger("vizora.isup-api")

Viewer = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
AdminOnly = Annotated[CurrentUser, Depends(require_role("admin"))]
Db = Annotated[AsyncSession, Depends(get_db)]


async def _apply() -> None:
    """Kalitlarni darhol bridge'ga (5 s sikl kutilmasin). Xato so'rovni yiqitmaydi."""
    try:
        await isup.push_keys(force=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("ISUP kalitlari yuborilmadi: %s", exc)


async def _key_or_404(db: AsyncSession, kid: uuid.UUID) -> IsupKey:
    obj = await db.get(IsupKey, kid)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ISUP kalit topilmadi")
    return obj


async def _device_or_404(db: AsyncSession, did: uuid.UUID) -> IsupDevice:
    obj = await db.get(IsupDevice, did)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ISUP qurilma topilmadi")
    return obj


async def _check_zone(db: AsyncSession, fid: uuid.UUID | None) -> None:
    if fid is not None and await db.get(Zone, fid) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hudud mavjud emas")


# --- Kalitlar -----------------------------------------------------------------
async def _key_usage(db: AsyncSession) -> dict:
    """Kalit id -> {"devices": n, "cameras": n} — qurilma qaysi kalit bilan ulanishi
    bo'yicha (o'z `key_enc` i bor qurilma hech qaysi IsupKey'ni ishlatmaydi)."""
    std = dict((await db.execute(
        select(Zone.id, Zone.isup_key_id).where(Zone.isup_key_id.is_not(None)))).all())
    cams = dict((await db.execute(
        select(Camera.isup_device_id, func.count()).where(Camera.source_type == "isup")
        .group_by(Camera.isup_device_id))).all())
    use: dict = {}
    for dev_id, fid, kid, own in (await db.execute(
            select(IsupDevice.device_id, IsupDevice.zone_id, IsupDevice.key_id,
                   IsupDevice.key_enc.is_not(None)))).all():
        if own:
            continue
        eff = kid or std.get(fid)
        if eff is None:
            continue
        u = use.setdefault(eff, {"devices": 0, "cameras": 0})
        u["devices"] += 1
        u["cameras"] += cams.get(dev_id, 0)
    return use


@router.get("/keys", response_model=list[IsupKeyOut])
async def list_keys(db: Db, _: Viewer, zone_id: uuid.UUID | None = None) -> list[IsupKeyOut]:
    """Kalitlar hovuzi. `zone_id` — shu hudud ichidagi kalitlar + umumiy kalitlar."""
    stmt = select(IsupKey).order_by(IsupKey.created_at)
    if zone_id is not None:
        stmt = stmt.where((IsupKey.zone_id == zone_id) | (IsupKey.zone_id.is_(None)))
    keys = (await db.execute(stmt)).scalars().all()
    fac_rows = (await db.execute(
        select(Zone.id, Zone.name, Branch.name, Zone.isup_key_id)
        .join(Branch, Branch.id == Zone.branch_id, isouter=True))).all()
    names = {fid: fname for fid, fname, _, _ in fac_rows}
    std_of: dict = {}
    for fid, fname, ename, kid in fac_rows:
        if kid is not None:
            std_of.setdefault(kid, []).append(ZoneRef(id=fid, name=fname, branch_name=ename))
    use = await _key_usage(db)
    return [
        IsupKeyOut(id=k.id, label=k.label, created_at=k.created_at, rotated_at=k.rotated_at,
                   zone_id=k.zone_id, zone_name=names.get(k.zone_id),
                   zones=std_of.get(k.id, []), devices=use.get(k.id, {}).get("devices", 0),
                   cameras=use.get(k.id, {}).get("cameras", 0))
        for k in keys
    ]


@router.post("/keys", response_model=IsupKeySecret, status_code=status.HTTP_201_CREATED)
async def create_key(payload: IsupKeyCreate, db: Db, user: AdminOnly) -> IsupKeySecret:
    if payload.zone_id is not None and await db.get(Zone, payload.zone_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Hudud mavjud emas")
    plain = payload.key or isup.generate_key()
    obj = IsupKey(label=payload.label, key_enc=crypto.encrypt(plain), zone_id=payload.zone_id)
    db.add(obj)
    await db.commit()
    log.info("ISUP kalit yaratildi: %s (%s, hudud %s) — %s", obj.label, obj.id, obj.zone_id, user.username)
    return IsupKeySecret(id=obj.id, key=plain, label=obj.label)


@router.patch("/keys/{kid}", response_model=IsupKeyOut)
async def update_key(kid: uuid.UUID, payload: IsupKeyUpdate, db: Db, _: AdminOnly) -> IsupKeyOut:
    obj = await _key_or_404(db, kid)
    if payload.label is not None:
        obj.label = payload.label
    await db.commit()
    return IsupKeyOut(id=obj.id, label=obj.label, created_at=obj.created_at, rotated_at=obj.rotated_at)


@router.get("/keys/{kid}/reveal", response_model=IsupKeySecret)
async def reveal_key(kid: uuid.UUID, db: Db, user: AdminOnly) -> IsupKeySecret:
    obj = await _key_or_404(db, kid)
    log.info("ISUP kalit ko'rildi: %s (%s) — %s", obj.label, obj.id, user.username)
    return IsupKeySecret(id=obj.id, key=crypto.decrypt(obj.key_enc))


@router.post("/keys/{kid}/rotate", response_model=IsupKeySecret)
async def rotate_key(kid: uuid.UUID, payload: IsupKeyRotate, db: Db, user: AdminOnly) -> IsupKeySecret:
    """Yangi kalit. DIQQAT: shu kalitli hududlarning HAMMA qurilmasi uziladi va
    qurilmalarda kalit qo'lda yangilanmaguncha ulana olmaydi."""
    from datetime import datetime, timezone

    obj = await _key_or_404(db, kid)
    plain = payload.key or isup.generate_key()
    obj.key_enc = crypto.encrypt(plain)
    obj.rotated_at = datetime.now(timezone.utc)
    await db.commit()
    log.warning("ISUP kalit ALMASHTIRILDI: %s (%s) — %s", obj.label, obj.id, user.username)
    await _apply()
    return IsupKeySecret(id=obj.id, key=plain)


@router.delete("/keys/{kid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(kid: uuid.UUID, db: Db, user: AdminOnly) -> None:
    obj = await _key_or_404(db, kid)
    await db.delete(obj)  # hududlarda FK SET NULL — ular zaxira kalitga qaytadi
    await db.commit()
    log.warning("ISUP kalit o'chirildi: %s (%s) — %s", obj.label, kid, user.username)
    await _apply()


# --- Hududlar -----------------------------------------------------------------
@router.get("/zones", response_model=list[ZoneIsupOut])
async def list_zones(db: Db, _: Viewer) -> list[ZoneIsupOut]:
    rows = (await db.execute(
        select(Zone, Branch.name)
        .join(Branch, Branch.id == Zone.branch_id, isouter=True)
        .order_by(Branch.name, Zone.name))).all()
    counts = dict((await db.execute(
        select(IsupDevice.zone_id, func.count()).where(IsupDevice.zone_id.is_not(None))
        .group_by(IsupDevice.zone_id))).all())
    return [
        ZoneIsupOut(id=f.id, name=f.name, code=f.code, branch_name=en, isup_key_id=f.isup_key_id,
                       isup_prefix=f.isup_prefix, devices=counts.get(f.id, 0))
        for f, en in rows
    ]


@router.put("/zones/{fid}", response_model=ZoneIsupOut)
async def set_zone(fid: uuid.UUID, payload: ZoneIsupUpdate, db: Db, user: AdminOnly) -> ZoneIsupOut:
    f = await db.get(Zone, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    if payload.isup_key_id is not None:
        await _key_or_404(db, payload.isup_key_id)
    f.isup_key_id = payload.isup_key_id
    f.isup_prefix = payload.isup_prefix or None
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu prefiks boshqa hududda band")
    log.info("Hudud %s ISUP: kalit=%s prefiks=%s — %s", f.name, f.isup_key_id, f.isup_prefix, user.username)
    await _apply()
    ent = await db.get(Branch, f.branch_id) if f.branch_id else None
    return ZoneIsupOut(id=f.id, name=f.name, code=f.code, branch_name=ent.name if ent else None,
                          isup_key_id=f.isup_key_id, isup_prefix=f.isup_prefix)


@router.post("/zones/{fid}/keys", response_model=list[IsupKeySecret], status_code=status.HTTP_201_CREATED)
async def generate_zone_keys(fid: uuid.UUID, payload: ZoneKeysCreate, db: Db, user: AdminOnly) -> list[IsupKeySecret]:
    """Hudud ICHIDA xohlagancha kalit generatsiya qiladi (1–50).

    `make_default` — birinchisi hudud standarti bo'ladi (eskisi o'rnini bosadi: standart
    kalitdagi qurilmalar uziladi va yangi kalit yozilmaguncha ulanmaydi). Qolganlari bo'sh
    turadi — alohida qurilmalarga beriladi."""
    f = await db.get(Zone, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    n_have = (await db.execute(select(func.count()).select_from(IsupKey).where(IsupKey.zone_id == fid))).scalar() or 0
    base = (payload.label or f"{f.name} kaliti").strip()
    out = []
    for i in range(payload.count):
        plain = isup.generate_key()
        label = base if payload.count == 1 and n_have == 0 else f"{base} {n_have + i + 1}"
        k = IsupKey(label=label, key_enc=crypto.encrypt(plain), zone_id=fid)
        db.add(k)
        await db.flush()
        out.append(IsupKeySecret(id=k.id, key=plain, label=label))
    if payload.make_default and out:
        f.isup_key_id = out[0].id
    await db.commit()
    log.info("Hudud %s: %d ta ISUP kalit generatsiya qilindi (standart=%s) — %s",
             f.name, len(out), payload.make_default, user.username)
    await _apply()
    return out


@router.post("/zones/{fid}/key", response_model=IsupKeySecret, status_code=status.HTTP_201_CREATED)
async def create_zone_key(fid: uuid.UUID, db: Db, user: AdminOnly) -> IsupKeySecret:
    """Bitta kalit generatsiya qilib hudud standarti qiladi (eski tugmalar uchun)."""
    return (await generate_zone_keys(fid, ZoneKeysCreate(count=1, make_default=True), db, user))[0]


@router.get("/zones/{fid}/key", response_model=IsupKeySecret)
async def reveal_zone_key(fid: uuid.UUID, db: Db, user: AdminOnly) -> IsupKeySecret:
    f = await db.get(Zone, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    if f.isup_key_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hududga ISUP kalit biriktirilmagan")
    k = await _key_or_404(db, f.isup_key_id)
    log.info("Hudud %s ISUP kaliti ko'rildi (%s) — %s", f.name, k.id, user.username)
    return IsupKeySecret(id=k.id, key=crypto.decrypt(k.key_enc), label=k.label)


# --- Ulanish ma'lumotlari (hudud ichida: Device ID + o'z kaliti) --------------
# Admin o'rnatuvchiga kamerani EMAS, tayyor juftlikni beradi: noyob Device ID + kalit.
# Nom so'ralmaydi — qurilma ulanganda kamera o'zi qo'shiladi ([[isup._auto_cameras]]).
# Har juftlik bitta qurilmaga: Device ID noyob, kalit boshqa joyda ishlamaydi.
async def _slots_out(db: AsyncSession, fid: uuid.UUID) -> list[dict]:
    state = await isup.bridge_state()
    devs = (await db.execute(select(IsupDevice).where(IsupDevice.zone_id == fid)
                             .order_by(IsupDevice.device_id))).scalars().all()
    keys = {k.id: k for k in (await db.execute(select(IsupKey).where(IsupKey.zone_id == fid))).scalars()}
    cams: dict = {}
    for cid, name, dev_id, st in (await db.execute(
            select(Camera.id, Camera.name, Camera.isup_device_id, Camera.status)
            .where(Camera.source_type == "isup", Camera.isup_device_id.in_([d.device_id for d in devs] or [""]))
            .order_by(Camera.isup_channel))).all():
        cams.setdefault(dev_id, []).append({"id": str(cid), "name": name, "status": st})
    out = []
    for d in devs:
        k = keys.get(d.key_id)
        live = (state or {}).get(d.device_id)
        out.append({
            "id": str(d.id), "device_id": d.device_id, "label": d.label, "blocked": d.blocked,
            "key": crypto.decrypt(k.key_enc) if k else None,       # o'z kaliti (bo'lsa)
            "own_key": k is not None,
            "online": bool(live), "ip": (live or {}).get("ip"),
            "last_online_at": d.last_online_at.isoformat() if d.last_online_at else None,
            "cameras": cams.get(d.device_id, []),
            "status": "blocked" if d.blocked else "camera" if cams.get(d.device_id) else
                      "online" if live else "used" if d.last_online_at else "waiting",
            "created_at": d.created_at.isoformat() if d.created_at else None,
        })
    return out


@router.get("/zones/{fid}/slots")
async def list_slots(fid: uuid.UUID, db: Db, user: AdminOnly) -> list[dict]:
    if await db.get(Zone, fid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    return await _slots_out(db, fid)


@router.post("/zones/{fid}/slots", status_code=status.HTTP_201_CREATED)
async def create_slots(fid: uuid.UUID, payload: ZoneKeysCreate, db: Db, user: AdminOnly) -> list[dict]:
    """N ta ulanish ma'lumoti: har biriga noyob Device ID (`<hudud kodi>-001` …) va
    alohida kalit. Qaytgan ro'yxat o'rnatuvchiga beriladi."""
    f = await db.get(Zone, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    taken = set((await db.execute(select(IsupDevice.device_id))).scalars())
    taken |= set((await db.execute(select(Camera.isup_device_id).where(Camera.isup_device_id.is_not(None)))).scalars())
    ids = isup.next_device_ids(isup.slot_base(f), taken, payload.count)
    made = []
    for dev_id in ids:
        plain = isup.generate_key()
        k = IsupKey(label=f"{dev_id} kaliti", key_enc=crypto.encrypt(plain), zone_id=fid)
        db.add(k)
        await db.flush()
        db.add(IsupDevice(device_id=dev_id, zone_id=fid, key_id=k.id, source="slot"))
        made.append({"device_id": dev_id, "key": plain})
    await db.commit()
    log.info("Hudud %s: %d ta ISUP ulanish ma'lumoti yaratildi (%s…) — %s",
             f.name, len(made), ids[0] if ids else "", user.username)
    await _apply()
    return made


# --- Qurilmalar ---------------------------------------------------------------
async def _keys_ctx(db: AsyncSession):
    keys, labels = {}, {}
    for k in (await db.execute(select(IsupKey))).scalars():
        labels[k.id] = k.label
        try:
            keys[k.id] = crypto.decrypt(k.key_enc)
        except Exception:  # noqa: BLE001
            pass
    facs = {f.id: f for f in (await db.execute(select(Zone))).scalars()}
    return keys, facs, labels


def _dev_ns(d: IsupDevice):
    from types import SimpleNamespace

    plain = None
    if d.key_enc:
        try:
            plain = crypto.decrypt(d.key_enc)
        except Exception:  # noqa: BLE001
            plain = None
    return SimpleNamespace(device_id=d.device_id, zone_id=d.zone_id, blocked=d.blocked,
                           key_plain=plain, key_id=d.key_id)


def _key_label(ns, src: str, facs: dict, labels: dict) -> str | None:
    if src == "assigned":
        return labels.get(ns.key_id)
    if src == "zone":
        fac = facs.get(ns.zone_id)
        return labels.get(fac.isup_key_id) if fac else None
    return None


@router.get("/devices", response_model=IsupOverview)
async def overview(db: Db, _: Viewer) -> IsupOverview:
    """Reestr + jonli holat (bridge) + rad etilgan urinishlar (kalitsiz/xato kalit)."""
    state = await isup.bridge_state(max_age=0)
    keys, facs, labels = await _keys_ctx(db)
    cams = dict((await db.execute(
        select(Camera.isup_device_id, func.count()).where(Camera.source_type == "isup")
        .group_by(Camera.isup_device_id))).all())
    devs = (await db.execute(select(IsupDevice).order_by(IsupDevice.device_id))).scalars().all()
    out = []
    for d in devs:
        live = (state or {}).get(d.device_id) or {}
        ns = _dev_ns(d)
        _, src = isup.resolve_key(ns, facs, keys)
        fac = facs.get(d.zone_id)
        out.append(IsupDeviceOut(
            id=d.id, device_id=d.device_id, label=d.label, zone_id=d.zone_id,
            zone_name=fac.name if fac else None, blocked=d.blocked, has_own_key=d.has_own_key,
            key_id=d.key_id, key_label=_key_label(ns, src, facs, labels),
            key_source=src, source=d.source, online=bool(live),
            connected_with=live.get("key_source") or None, ip=live.get("ip") or None,
            model=live.get("full_serial") or None, firmware=live.get("firmware") or None,
            cameras=cams.get(d.device_id, 0), last_online_at=d.last_online_at, created_at=d.created_at,
        ))
    known = {d.device_id for d in devs}
    rejected = [
        IsupRejected(device_id=r["id"], reason=r.get("reason") or "", count=int(r.get("count") or 0),
                     age=int(r.get("age") or 0), registered=r["id"] in known)
        for r in isup._rejected  # bridge_state() yangiladi
    ]
    return IsupOverview(
        bridge_ok=state is not None,
        default_key_allowed=settings.isup_allow_default_key,
        server_host=settings.isup_public_host, server_port=settings.isup_cms_port,
        devices=out, rejected=sorted(rejected, key=lambda r: r.age),
    )


@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def create_device(payload: IsupDeviceCreate, db: Db, user: AdminOnly) -> dict:
    await _check_zone(db, payload.zone_id)
    exists = (await db.execute(select(IsupDevice.id).where(IsupDevice.device_id == payload.device_id))).first()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bu Device ID allaqachon reestrda")
    obj = IsupDevice(device_id=payload.device_id, label=payload.label, zone_id=payload.zone_id, source="manual")
    db.add(obj)
    plain = None
    try:
        if payload.own_key:
            # O'z kaliti = hudud ichida shu qurilma uchun yangi kalit (hudud kalitlari
            # ro'yxatida ko'rinadi, Bo'sh/Band hisobiga kiradi).
            plain = await isup.ensure_device(db, obj.device_id, obj.zone_id, "new")
        elif payload.key_id:
            await isup.ensure_device(db, obj.device_id, obj.zone_id, str(payload.key_id))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    log.info("ISUP qurilma qo'shildi: %s — %s", obj.device_id, user.username)
    await _apply()
    return {"id": str(obj.id), "key": plain}


@router.patch("/devices/{did}")
async def update_device(did: uuid.UUID, payload: IsupDeviceUpdate, db: Db, user: AdminOnly) -> dict:
    """`key`: "default" — hudud standarti; "new" — hudud ichida yangi kalit; <uuid> —
    mavjud kalit. Eski "o'z kaliti" (`key_enc`) kalit tanlanganda olib tashlanadi."""
    obj = await _device_or_404(db, did)
    data = payload.model_dump(exclude_unset=True)
    reject_null_columns(obj, data)
    if "zone_id" in data:
        await _check_zone(db, data["zone_id"])
        obj.zone_id = data["zone_id"]
    if "label" in data:
        obj.label = data["label"]
    if data.get("blocked") is not None:
        obj.blocked = data["blocked"]
    plain = None
    if data.get("key"):
        obj.key_enc = None
        try:
            plain = await isup.ensure_device(db, obj.device_id, obj.zone_id, data["key"])
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    log.info("ISUP qurilma %s o'zgartirildi (%s) — %s", obj.device_id, sorted(data), user.username)
    await _apply()
    return {"id": str(obj.id), "key": plain}


@router.delete("/devices/{did}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(did: uuid.UUID, db: Db, user: AdminOnly) -> None:
    obj = await _device_or_404(db, did)
    n = (await db.execute(select(func.count()).select_from(Camera)
                          .where(Camera.source_type == "isup", Camera.isup_device_id == obj.device_id))).scalar()
    if n:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Qurilmada {n} ta kamera bor — avval kameralarni o'chiring")
    kid = obj.key_id
    await db.delete(obj)
    await db.flush()
    # Ulanish ma'lumotining o'z kaliti boshqa hech kimga kerak bo'lmasa — u ham o'chadi.
    if kid is not None:
        used = (await db.execute(select(func.count()).select_from(IsupDevice).where(IsupDevice.key_id == kid))).scalar()
        std = (await db.execute(select(func.count()).select_from(Zone).where(Zone.isup_key_id == kid))).scalar()
        if not used and not std:
            k = await db.get(IsupKey, kid)
            if k is not None and k.zone_id is not None:
                await db.delete(k)
    await db.commit()
    log.info("ISUP qurilma o'chirildi: %s — %s", obj.device_id, user.username)
    await _apply()


@router.get("/effective-key", response_model=IsupEffectiveKey)
async def effective_key(db: Db, user: AdminOnly,
                        device_id: str = Query(pattern=r"^[A-Za-z0-9_-]{1,64}$"),
                        zone_id: uuid.UUID | None = None,
                        key: str | None = Query(default=None, pattern=r"^(default|[0-9a-fA-F-]{36})$")) -> IsupEffectiveKey:
    """Qurilmaga qaysi kalit yoziladi (kamera/qurilma sozlash yo'riqnomasi uchun).

    Reestrda bo'lsa — uning kaliti; bo'lmasa `zone_id` (kamera formasidagi hudud)
    yoki prefiks bo'yicha hudud kaliti; bo'lmasa zaxira. `key` — formada hozir
    tanlangan (hali saqlanmagan) kalit: "default" yoki kalit id."""
    from types import SimpleNamespace

    keys, facs, labels = await _keys_ctx(db)
    d = (await db.execute(select(IsupDevice).where(IsupDevice.device_id == device_id))).scalars().first()
    if d is not None:
        ns = _dev_ns(d)
        if zone_id is not None:
            ns.zone_id = zone_id
    else:
        fid = zone_id
        if fid is None:
            for f in sorted((f for f in facs.values() if f.isup_prefix), key=lambda f: -len(f.isup_prefix)):
                if device_id.startswith(f.isup_prefix):
                    fid = f.id
                    break
        ns = SimpleNamespace(device_id=device_id, zone_id=fid, blocked=False, key_plain=None, key_id=None)
    if key == "default":
        ns.key_plain, ns.key_id = None, None
    elif key:
        ns.key_plain, ns.key_id = None, uuid.UUID(key)
    plain, src = isup.resolve_key(ns, facs, keys)
    kid = ns.key_id if src == "assigned" else (facs[ns.zone_id].isup_key_id if src == "zone" else None)
    log.info("ISUP qurilma kaliti ko'rildi: %s (%s) — %s", device_id, src, user.username)
    return IsupEffectiveKey(device_id=device_id, key=plain, source=src, key_id=kid,
                            key_label=_key_label(ns, src, facs, labels))
