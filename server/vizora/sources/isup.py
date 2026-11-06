"""ISUP 5.0 kameralar — isup-bridge bilan aloqa va oqimlarni boshqarish.

ISUP'da Hikvision qurilmasi bulut o'rniga O'ZI bizning serverga ulanadi (isup-bridge
konteyneri, `isup/` papkasi). Bridge oqimni transkodsiz MediaMTX'ga publish qiladi:

    isup/<DeviceID>/<kanal>       asosiy oqim
    isup/<DeviceID>/<kanal>/sub   substream

Kamera (`source_type="isup"`) uchun `rtsp_url_enc` shu path'larga ishora qiladi, ya'ni
`cam/<id>` oddiy RTSP kamera kabi ishlaydi (transkod, probe, snapshot, WS, HLS — hammasi
o'zgarishsiz). Bu modul faqat QAYSI oqim ochiq turishini hal qiladi va bridge'ga
"want" ro'yxati qilib yuboradi:

  * substream — har yoqilgan ISUP kamera uchun doim (arzon; onlayn holat, devor,
    snapshot shundan);
  * asosiy oqim — faqat kimdir ko'rayotganda ([[touch]] yoki MediaMTX'da o'quvchi
    bor) va oxirgi tomoshabindan keyin `isup_main_idle_seconds` gacha. Kodek hali
    aniqlanmagan kamerada ham ochiq turadi — probe uni aniqlashi uchun.

ISUP'da oqim ~0.23 s da ochiladi, shuning uchun so'ralganda ochish sezilmaydi, 24/7
ochiq tursa esa 100 kamera × 4 Mbit/s = 420 Mbit/s bo'lardi.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Iterable
from types import SimpleNamespace

import httpx
from sqlalchemy import select

from vizora.core.config import settings

log = logging.getLogger("vizora.isup")

# Device ID path nomiga va bridge'ning shell buyrug'iga tushadi — faqat xavfsiz belgilar.
# Bridge ham xuddi shu qoidani tekshiradi (`valid_id`).
DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")   # \Z — oxirida "\n" o'tmasin

MAIN, SUB = 0, 1
CODEC_PROBE_MAX = 4   # bir vaqtda kodek aniqlanayotgan (tomoshabinsiz) asosiy oqimlar
# Qurilma ma'lumoti ishonchsiz (kanal ro'yxatini qurilmaning o'zi beradi): bitta qurilma
# uchun avtomatik kamera soni va bir siklda reestrga yoziladigan yangi qurilmalar cheklangan.
MAX_AUTO_CHANNELS = 64
MAX_NEW_DEVICES_PER_SYNC = 20

_hot: dict[tuple[str, int], float] = {}     # (dev, kanal) -> oxirgi tomosha (monotonic)
_state: dict | None = None                   # oxirgi /devices javobi
_rejected: list[dict] = []                   # rad etilgan ulanish urinishlari (panel uchun)
_bridge_keys_set: bool | None = None         # bridge kalitlarni oldimi (qayta ishga tushsa False)
_state_at = 0.0
_last_want: str | None = None
_last_push = 0.0
_lock = asyncio.Lock()


def enabled() -> bool:
    return bool(settings.isup_bridge_url)


def path_for(dev: str, ch: int, sub: bool = False) -> str:
    return f"isup/{dev}/{int(ch)}" + ("/sub" if sub else "")


def rtsp_url(dev: str, ch: int, sub: bool = False) -> str:
    return f"rtsp://{settings.mediamtx_rtsp_host}:8554/{path_for(dev, ch, sub)}"


def fill_urls(cam) -> None:
    """ISUP kameraning RTSP URL'larini Device ID + kanaldan to'ldiradi (create/update)."""
    from vizora.core import crypto

    dev, ch = cam.isup_device_id, cam.isup_channel or 1
    cam.rtsp_url_enc = crypto.encrypt(rtsp_url(dev, ch))
    cam.rtsp_sub_url_enc = crypto.encrypt(rtsp_url(dev, ch, sub=True)) if settings.isup_substream else None


def _client() -> httpx.AsyncClient:
    headers = {"X-Bridge-Token": settings.isup_bridge_token} if settings.isup_bridge_token else None
    return httpx.AsyncClient(base_url=settings.isup_bridge_url, timeout=3.0, headers=headers)


async def bridge_state(max_age: float = 2.0) -> dict | None:
    """Ulangan qurilmalar: {device_id: {...}}. Bridge javob bermasa — None.

    None va {} farqli: None "bilmaymiz" (holat o'zgartirilmaydi), {} esa "hech qaysi
    qurilma ulanmagan" (ISUP kameralar oflayn)."""
    global _state, _state_at, _rejected, _bridge_keys_set
    if not enabled():
        return None
    if _state is not None and time.monotonic() - _state_at < max_age:
        return _state
    try:
        async with _client() as c:
            r = await c.get("/devices")
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("isup-bridge javob bermadi: %s", exc)
        _state, _state_at = None, time.monotonic()
        return None
    _state = {d["id"]: d for d in data.get("devices", []) if isinstance(d, dict) and "id" in d}
    _rejected = [r for r in data.get("rejected", []) if isinstance(r, dict) and "id" in r]
    _bridge_keys_set = bool(data.get("keys_set"))
    _state_at = time.monotonic()
    return _state


def want_lines(cams: Iterable, now: float, readers: dict[str, int] | None = None) -> list[str]:
    """Bridge'ga yuboriladigan "want" ro'yxati (sof funksiya — test qilinadi).

    `cams` — yoqilgan ISUP kameralar (isup_device_id, isup_channel, codec).
    `readers` — MediaMTX path nomi -> o'quvchilar soni (asosiy oqimni tirik tutadi).
    """
    idle = settings.isup_main_idle_seconds
    out: set[str] = set()
    # Kodek aniqlash uchun ochiladigan asosiy oqimlar cheklangan: 16 kanalli NVR ulanganda
    # 16 x 4 Mbit/s birdaniga ochilmasin. Oyna har daqiqada SURILADI — oflayn kanal
    # (oqimi hech kelmaydi, kodeki "auto" qoladi) navbatni abadiy egallab olmasin.
    cams = list(cams)
    auto = sorted((c.isup_device_id or "", int(c.isup_channel or 1)) for c in cams if (c.codec or "auto") == "auto")
    if len(auto) > CODEC_PROBE_MAX:
        start = (int(now // 60) * CODEC_PROBE_MAX) % len(auto)
        probe_set = {auto[(start + i) % len(auto)] for i in range(CODEC_PROBE_MAX)}
    else:
        probe_set = set(auto)
    for cam in cams:
        dev, ch = cam.isup_device_id, int(cam.isup_channel or 1)
        if not dev or not DEVICE_ID_RE.match(dev):
            continue
        if settings.isup_substream:
            out.add(f"{dev} {ch} {SUB}")
        key = (dev, ch)
        if readers and readers.get(path_for(dev, ch), 0) > 0:
            _hot[key] = now
        hot = key in _hot and now - _hot[key] < idle
        probe = (dev, ch) in probe_set
        # "Doim issiq" (Camera.keep_warm) — asosiy oqim tomoshabinsiz ham ochiq turadi.
        warm = bool(getattr(cam, "keep_warm", False))
        if idle <= 0 or hot or probe or warm:
            out.add(f"{dev} {ch} {MAIN}")
    return sorted(out)


# --- Kalitlar ([[vizora.models.isup]]) -------------------------------------------
KEY_RE = re.compile(r"^[A-Za-z0-9]{8,32}\Z")
PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{2,32}\Z")
_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"  # 0/O, 1/l/I yo'q

_last_keys: str | None = None
_last_keys_push = 0.0


def generate_key(length: int = 16) -> str:
    """Qurilma web-interfeysiga qo'lda yoziladi — chalkash belgilarsiz (0/O, 1/l)."""
    import secrets

    return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(length))


def resolve_key(dev, zones: dict, keys: dict) -> tuple[str | None, str]:
    """Panelda ro'yxatdagi qurilmaning kaliti: (kalit | None, manba).

    `dev` — IsupDevice (key_plain, zone_id, blocked); `zones` — id -> hudud
    (isup_key_id); `keys` — IsupKey id -> ochiq kalit. Manba: blocked | device |
    assigned (qurilmaga tanlangan hudud kaliti) |
    zone | default | none."""
    if dev.blocked:
        return None, "blocked"
    if dev.key_plain:
        return dev.key_plain, "device"
    if getattr(dev, "key_id", None) in keys:
        return keys[dev.key_id], "assigned"
    fac = zones.get(dev.zone_id)
    if fac is not None and fac.isup_key_id in keys:
        return keys[fac.isup_key_id], "zone"
    if settings.isup_allow_default_key and settings.isup_default_key:
        return settings.isup_default_key, "default"
    return None, "none"


def key_lines(devices: Iterable, zones: dict, keys: dict) -> list[str]:
    """Bridge'ga `PUT /keys` matni (sof funksiya — test qilinadi).

    Ro'yxatdagi qurilma uchun kalit to'liq shu yerda hal qilinadi (`dev`/`block`) —
    aks holda hududi kalitsiz qurilma BOSHQA hududning prefiksiga tushib, begona kalit
    bilan ulanib ketishi mumkin edi. Prefiks va zaxira faqat ro'yxatda yo'q qurilmalarga.
    """
    out: list[str] = []
    for dev in devices:
        if not DEVICE_ID_RE.match(dev.device_id or ""):
            continue
        key, src = resolve_key(dev, zones, keys)
        if key and KEY_RE.match(key):
            out.append(f"dev {dev.device_id} {key}")
        elif src != "none" or not settings.isup_allow_default_key:
            out.append(f"block {dev.device_id}")
        # src == "none" + zaxira yoqiq, lekin backend kalitni bilmaydi -> bridge zaxirasi
    for fac in zones.values():
        if fac.isup_prefix and PREFIX_RE.match(fac.isup_prefix) and fac.isup_key_id in keys:
            out.append(f"prefix {fac.isup_prefix} {keys[fac.isup_key_id]}")
    out.append(f"default {'on' if settings.isup_allow_default_key else 'off'}")
    return out


async def _load_keys_state():
    """DB'dan: (qurilmalar, hududlar, kalitlar) — deshifrlangan, sessiyasiz."""
    from vizora.core.db import SessionLocal
    from vizora.models.zone import Zone
    from vizora.models.isup import IsupDevice, IsupKey
    from vizora.core import crypto

    async with SessionLocal() as db:
        keys = {}
        for k in (await db.execute(select(IsupKey))).scalars():
            try:
                keys[k.id] = crypto.decrypt(k.key_enc)
            except Exception:  # noqa: BLE001 — AES kalit almashgan bo'lsa shu kalit ishlamaydi
                log.warning("ISUP kalit %s ochilmadi (AES_KEY_HEX o'zgarganmi?)", k.id)
        facs = {
            f.id: SimpleNamespace(id=f.id, isup_key_id=f.isup_key_id, isup_prefix=f.isup_prefix)
            for f in (await db.execute(
                select(Zone).where((Zone.isup_key_id.is_not(None)) | (Zone.isup_prefix.is_not(None)))
            )).scalars()
        }
        devs = []
        for d in (await db.execute(select(IsupDevice))).scalars():
            plain = None
            if d.key_enc:
                try:
                    plain = crypto.decrypt(d.key_enc)
                except Exception:  # noqa: BLE001
                    log.warning("ISUP qurilma %s kaliti ochilmadi", d.device_id)
            devs.append(SimpleNamespace(device_id=d.device_id, zone_id=d.zone_id,
                                        blocked=d.blocked, key_plain=plain, key_id=d.key_id))
    return devs, facs, keys


def slot_base(zone) -> str:
    """Ulanish ma'lumoti Device ID'si uchun asos: hudud prefiksi yoki kodidan (xavfsiz belgilar)."""
    base = (zone.isup_prefix or "").rstrip("-_") or re.sub(r"[^A-Za-z0-9]", "", zone.code or "") or "ISUP"
    return base[:24].upper()


def next_device_ids(base: str, taken: set[str], n: int) -> list[str]:
    """`B001-001`, `B001-002`, … — band bo'lganlarini o'tkazib (sof funksiya)."""
    out, i = [], 1
    while len(out) < n:
        cand = f"{base}-{i:03d}"
        if cand not in taken:
            out.append(cand)
        i += 1
    return out


def auto_channels(info: dict) -> list[tuple[int, str]] | None:
    """Ulangan qurilmaning qaysi kanallariga kamera ochiladi (sof funksiya).

    NVR bitta Device ID bilan ulanadi, ichida N kanal: bridge ulanishdan keyin kanallar
    ro'yxatini so'raydi (`info`: pending → ok/failed). Ro'yxat hali kelmagan bo'lsa
    None — kutamiz (aks holda NVR uchun faqat 1-kanal ochilib qolardi), lekin 30 s dan
    oshsa 1-kanal bilan davom etamiz. Kanal raqami ISAPI'dagi id (1..N)."""
    if info.get("info", "ok") == "pending" and int(info.get("online_seconds") or 0) < 30:
        return None
    ip = [c for c in info.get("ip_channels") or [] if int(c.get("id") or 0) > 0]
    if ip:
        return [(int(c["id"]), (c.get("name") or "").strip()[:80])
                for c in sorted(ip, key=lambda c: int(c["id"]))][:MAX_AUTO_CHANNELS]
    total = int(info.get("channels_total") or 0)
    if total > 1:
        start = int(info.get("start_channel") or 1) or 1
        return [(start + i, "") for i in range(min(total, MAX_AUTO_CHANNELS))]
    return [(1, "")]


async def _auto_cameras(state: dict) -> None:
    """Reestrdagi (hududli) qurilma ulandi-yu kamerasi yo'q — kamerani o'zi qo'shadi.

    Admin o'rnatuvchiga faqat Device ID + kalit beradi va qaysi kamera ulanishini
    bilmaydi; shuning uchun kamera vaqtinchalik nom bilan paydo bo'ladi (keyin
    o'zgartiriladi). NVR bo'lsa — har kanal uchun alohida kamera ([[auto_channels]])."""
    if not settings.isup_auto_camera or not state:
        return
    from vizora.core.db import SessionLocal
    from vizora.models.camera import Camera
    from vizora.models.zone import Zone
    from vizora.models.isup import IsupDevice
    from vizora.media import mediamtx

    online = [d for d in state if DEVICE_ID_RE.match(d)]
    if not online:
        return
    created = []
    async with SessionLocal() as db:
        have = set((await db.execute(
            select(Camera.isup_device_id).where(Camera.source_type == "isup", Camera.isup_device_id.in_(online))
        )).scalars())
        rows = (await db.execute(
            select(IsupDevice, Zone.name).join(Zone, Zone.id == IsupDevice.zone_id)
            .where(IsupDevice.device_id.in_(online), IsupDevice.blocked.is_(False))
        )).all()
        for dev, fname in rows:
            if dev.device_id in have:
                continue
            chans = auto_channels(state.get(dev.device_id) or {})
            if chans is None:
                continue  # NVR kanallari hali so'ralmoqda — keyingi siklda
            base = dev.label or f"{fname} — {dev.device_id}"
            for ch, chname in chans:
                cid = uuid.uuid4()
                name = base if len(chans) == 1 else f"{base} · {chname or f'CH{ch}'}"
                cam = Camera(id=cid, name=name[:200], zone_id=dev.zone_id,
                             source_type="isup", isup_device_id=dev.device_id, isup_channel=ch,
                             stream_path=f"cam/{cid}", status="offline", enabled=True)
                fill_urls(cam)
                db.add(cam)
                created.append(cam)
        if created:
            await db.commit()
    for cam in created:
        log.info("ISUP: %s ulandi — kamera avtomatik qo'shildi (%s)", cam.isup_device_id, cam.name)
        await mediamtx.apply_rtsp_paths(cam)



async def _register_seen(state: dict) -> None:
    """Bridge'da ulangan, lekin reestrda yo'q qurilmalarni yozib qo'yadi (prefiks
    bo'yicha hududi bilan) va `last_online_at` ni yangilaydi."""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from vizora.core.db import SessionLocal
    from vizora.models.zone import Zone
    from vizora.models.isup import IsupDevice

    if not state:
        return
    now = datetime.now(timezone.utc)
    ids = [d for d in state if DEVICE_ID_RE.match(d)]
    if not ids:
        return
    async with SessionLocal() as db:
        known = set((await db.execute(
            select(IsupDevice.device_id).where(IsupDevice.device_id.in_(ids)))).scalars())
        prefixes = (await db.execute(
            select(Zone.id, Zone.isup_prefix).where(Zone.isup_prefix.is_not(None)))).all()
        added = 0
        for dev in ids:
            if dev in known or added >= MAX_NEW_DEVICES_PER_SYNC:
                continue
            fid = None
            for f_id, p in sorted(prefixes, key=lambda x: -len(x[1])):
                if dev.startswith(p):
                    fid = f_id
                    break
            db.add(IsupDevice(device_id=dev, zone_id=fid, source="prefix" if fid else "auto"))
            added += 1
            log.info("ISUP yangi qurilma reestrga yozildi: %s (hudud %s)", dev, fid or "—")
        await db.execute(update(IsupDevice).where(IsupDevice.device_id.in_(ids)).values(last_online_at=now))
        await db.commit()


async def ensure_device(db, device_id: str, zone_id=None, key_choice: str | None = None) -> str | None:
    """Kamera qo'shilganda qurilma reestrda bo'lsin (hududi kameradan) va (berilsa)
    uning kalitini o'rnatadi. Yangi kalit generatsiya qilinsa — ochiq matnini qaytaradi.

    `key_choice`: None — o'zgartirmaslik; "default" — hudud standarti; "new" — hudud
    ichida yangi kalit generatsiya qilib shu qurilmaga; <uuid> — mavjud kalit."""
    from vizora.models.isup import IsupDevice, IsupKey
    from vizora.core import crypto

    row = (await db.execute(select(IsupDevice).where(IsupDevice.device_id == device_id))).scalars().first()
    if row is None:
        row = IsupDevice(device_id=device_id, zone_id=zone_id, source="camera")
        db.add(row)
    elif row.zone_id is None and zone_id is not None:
        row.zone_id = zone_id
    if key_choice is None:
        return None
    if key_choice == "default":
        row.key_id = None
        return None
    if key_choice == "new":
        plain = generate_key()
        key = IsupKey(label=f"{device_id} kaliti", key_enc=crypto.encrypt(plain),
                      zone_id=row.zone_id or zone_id)
        db.add(key)
        await db.flush()
        row.key_id = key.id
        return plain
    try:
        kid = uuid.UUID(str(key_choice))
    except ValueError:
        raise ValueError("Noto'g'ri kalit tanlovi") from None
    if await db.get(IsupKey, kid) is None:
        raise ValueError("Tanlangan ISUP kalit topilmadi")
    row.key_id = kid
    return None


async def push_keys(*, force: bool = False) -> None:
    global _last_keys, _last_keys_push
    if not enabled():
        return
    devs, facs, keys = await _load_keys_state()
    body = "\n".join(key_lines(devs, facs, keys)) + "\n"
    now = time.monotonic()
    if not force and body == _last_keys and now - _last_keys_push < 30 and _bridge_keys_set is not False:
        return
    try:
        async with _client() as c:
            r = await c.put("/keys", content=body.encode(), headers={"Content-Type": "text/plain"})
            r.raise_for_status()
            res = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("isup-bridge'ga kalitlar yuborilmadi: %s", exc)
        return
    if body != _last_keys:
        log.info("ISUP kalitlari bridge'ga yuborildi (chiqarilgan qurilmalar: %s)", res.get("kicked", 0))
    _last_keys, _last_keys_push = body, now


async def _load_cams() -> list[SimpleNamespace]:
    from vizora.core.db import SessionLocal
    from vizora.models.camera import Camera

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Camera.isup_device_id, Camera.isup_channel, Camera.codec, Camera.keep_warm).where(
                    Camera.enabled.is_(True), Camera.source_type == "isup",
                )
            )
        ).all()
    return [SimpleNamespace(isup_device_id=d, isup_channel=c, codec=k, keep_warm=bool(w))
            for d, c, k, w in rows]


async def sync_once(*, force: bool = False) -> None:
    """Kerakli oqimlarni hisoblab bridge'ga yuboradi va MediaMTX path'larini ta'minlaydi."""
    global _last_want, _last_push
    if not enabled():
        return
    from vizora.media import mediamtx

    async with _lock:
        cams = await _load_cams()
        # ISUP kamera yo'q bo'lsa MediaMTX'ni har 5 s da varaqlamaymiz (oxirgisi
        # endigina o'chirilgan bo'lsa — bir marta, qolgan path'larni tozalash uchun).
        paths = await mediamtx.list_paths() if cams or _last_want not in (None, "\n") else []
        readers = {p.get("name"): len(p.get("readers") or []) for p in paths}
        # Bridge publish qiladigan path'lar oldindan bo'lishi shart (`paths: {}` —
        # e'lon qilinmagan nomga publish rad etiladi). Bridge ham yaratishga urinadi,
        # lekin MediaMTX API paroli faqat backend'da bo'lishi mumkin.
        for cam in cams if paths else ():  # bo'sh = MediaMTX javob bermadi
            if not cam.isup_device_id or not DEVICE_ID_RE.match(cam.isup_device_id):
                continue
            for sub in (False, True) if settings.isup_substream else (False,):
                name = path_for(cam.isup_device_id, cam.isup_channel or 1, sub)
                if name not in readers:
                    await mediamtx.safe_ensure_path(name)
        # O'chirilgan/o'chiq kameralarning path'lari qolib ketmasin — faqat oqim
        # kelmayotganlarini (publish qilinayotganiga tegmaymiz).
        expected = {
            path_for(c.isup_device_id, c.isup_channel or 1, sub)
            for c in cams if c.isup_device_id for sub in (False, True)
        }
        for p in paths:
            name = p.get("name") or ""
            if name.startswith("isup/") and name not in expected and not p.get("ready"):
                await mediamtx.remove_path(name)
        now = time.monotonic()
        for k in [k for k, t in _hot.items() if now - t > max(settings.isup_main_idle_seconds, 1) * 4]:
            _hot.pop(k, None)
        body = "\n".join(want_lines(cams, now, readers)) + "\n"
        # O'zgarmagan bo'lsa ham 30 s da bir yuboramiz — bridge qayta ishga tushgan
        # bo'lsa ro'yxatni yo'qotgan bo'ladi.
        if not force and body == _last_want and now - _last_push < 30:
            return
        try:
            async with _client() as c:
                r = await c.put("/want", content=body.encode(),
                                headers={"Content-Type": "text/plain"})
                r.raise_for_status()
        except httpx.HTTPError as exc:
            log.debug("isup-bridge'ga want yuborilmadi: %s", exc)
            return
        if body != _last_want:
            log.info("ISUP oqimlar ro'yxati yangilandi: %d ta", body.count("\n") - (body == "\n"))
        _last_want, _last_push = body, now


async def touch(cam, *, wait: bool = True) -> bool:
    """Tomoshabin keldi: asosiy oqimni ochtiradi va (wait=True) MediaMTX'da paydo
    bo'lishini kutadi. True = asosiy oqim tayyor.

    Kutish shart: `transcode=True` kamerada `cam/<id>` ning runOnDemand ffmpeg'i
    `isup/...` ga darhol ulanadi — u hali publish qilinmagan bo'lsa ffmpeg chiqib
    ketadi va tomoshabin timeout'gacha kutadi."""
    if not enabled() or not getattr(cam, "is_isup", False) or not cam.isup_device_id:
        return False
    from vizora.media import mediamtx

    _hot[(cam.isup_device_id, int(cam.isup_channel or 1))] = time.monotonic()
    name = path_for(cam.isup_device_id, cam.isup_channel or 1)
    try:
        await sync_once(force=True)
    except Exception as exc:  # noqa: BLE001 — oqim boshqaruvi so'rovni yiqitmasin
        log.warning("ISUP sync xatosi: %s", exc)
    if not wait:
        return False
    st = await bridge_state()
    if st is not None and cam.isup_device_id not in st:
        return False  # qurilma ulanmagan — kutish befoyda
    deadline = time.monotonic() + settings.isup_main_wait_seconds
    while True:
        try:
            p = await mediamtx.get_path(name)
        except httpx.HTTPError:
            p = None
        if p and p.get("ready"):
            return True
        if time.monotonic() >= deadline:
            log.info("ISUP asosiy oqim %ss ichida tayyor bo'lmadi: %s", settings.isup_main_wait_seconds, name)
            return False
        await asyncio.sleep(0.15)


def device_online(state: dict | None, dev: str | None) -> bool | None:
    """Probe uchun: qurilma ro'yxatdan o'tganmi (None — bridge'ni bilmaymiz)."""
    if state is None or not dev:
        return None
    return dev in state


async def run_sync() -> None:
    """Fon sikli (lifespan): har `isup_sync_seconds` da want ro'yxatini yangilaydi."""
    if not enabled():
        log.info("ISUP o'chiq (ISUP_BRIDGE_URL bo'sh)")
        return
    log.info("ISUP sinxron sikli ishga tushdi (bridge %s)", settings.isup_bridge_url)
    while True:
        try:
            # Avval kalitlar: bridge qayta ishga tushgan bo'lsa (`keys_set` false) ro'yxatdagi
            # qurilmalar kalitsiz qolib rad etilmasin.
            state = await bridge_state(max_age=0)
            await push_keys()
            if state:
                await _register_seen(state)
                await _auto_cameras(state)
            await sync_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("ISUP sync siklida xato: %s", exc)
        await asyncio.sleep(settings.isup_sync_seconds)
