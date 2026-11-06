"""Online/offline aniqlash + kodek avto-aniqlash.

Pi-agent (heartbeat) yo'qligi sababli server kameralarni o'zi faol tekshiradi:
  * Agar kamera ayni vaqtda MediaMTX'da oqayotgan bo'lsa (ready) -> online.
  * Aks holda ffprobe bilan RTSP'ga ulanib ko'riladi (TCP, qisqa timeout).
Mustahkamlik: bitta muvaffaqiyatsiz probe darrov offline qilmaydi —
faqat `offline_after_seconds` dan ko'p vaqt javob bo'lmasa offline bo'ladi.
Kodek aniqlansa cameras.codec yangilanadi; HEVC bo'lsa transcode avto-yoqiladi.
Holat o'zgarganda PostgreSQL yangilanadi va Redis pub/sub orqali e'lon qilinadi.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import redis.asyncio as aioredis
from sqlalchemy import select, update

from vizora.core.config import settings
from vizora.core.db import SessionLocal
from vizora.models.camera import Camera
from vizora.models.ezviz_account import EzvizAccount
from vizora.core import crypto
from vizora.media import mediamtx
from vizora.services import stats
from vizora.sources import isup
from vizora.sources.ezviz import manager as ezviz_manager
from vizora.sources.ezviz import tokens as ezviz_tokens
from vizora.sources.ezviz import vtm_cache

log = logging.getLogger("vizora.probe")

STATUS_CHANNEL = "camera:status"  # Redis pub/sub kanali (iste'molchilar uchun)


def _norm_codec(name: str | None) -> str | None:
    if not name:
        return None
    n = name.lower()
    if n in ("hevc", "h265"):
        return "h265"
    if n in ("h264", "avc"):
        return "h264"
    return n


async def probe_info(rtsp_url: str, timeout: float = 10.0) -> dict | None:
    """ffprobe bilan oqim ma'lumotini oladi. None = ulanib bo'lmadi.

    Qaytaradi: {"codec": "h264"|"h265"|..., "width": int, "height": int}.
    """
    rtsp_url = mediamtx.with_creds(rtsp_url)   # ISUP: URL MediaMTX'ning o'ziga ishora qiladi
    args = [
        "ffprobe", "-rtsp_transport", "tcp", "-v", "quiet",
        "-protocol_whitelist", mediamtx.FF_PROTOCOLS,  # file:/http: orqali SSRF yo'q
        "-rw_timeout", str(int(timeout * 1_000_000)),
        "-i", rtsp_url,
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height",
        "-of", "json",
    ]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 4)
        if proc.returncode != 0 or not out.strip():
            return None
        data = json.loads(out)
        streams = data.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        return {
            "codec": _norm_codec(s.get("codec_name")),
            "width": s.get("width"),
            "height": s.get("height"),
        }
    except (asyncio.TimeoutError, FileNotFoundError, OSError, ValueError):
        if proc:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        return None


# Path nomi -> oxirgi ko'rilgan bytesReceived (kadr oqimini aniqlash uchun).
# MediaMTX path "ready" bo'lishi yetarli EMAS: warm tutilgan kamerada bulut o'lsa
# ham ffmpeg RTSP publish'ni ochiq ushlab turadi → path "ready" qolaveradi, lekin
# yangi kadr (bayt) KELMAYDI. Shuning uchun "haqiqatan oqayotgan" = ready VA
# bytesReceived oshgan (siklllar orasida). Bu — "kadr o'qib tekshirish".
_prev_bytes: dict[str, int] = {}


def _base_path(name: str) -> str:
    """`cam/<id>/raw` | `cam/<id>/sub` -> `cam/<id>`.

    Kamera oqimi ASOSIY path'da bo'lishi shart emas: panel `/raw` (transkodsiz),
    ABR esa `/sub` dan o'qiydi, va XOM rejimda publisher FAQAT `/raw` ga yozadi —
    asosiy path umuman tayyor bo'lmaydi. Faqat asosiy nomni qaraganda oqib turgan
    kamera "oflayn" bo'lib ko'rinardi."""
    return "/".join(name.split("/")[:2])


async def _ready_paths_bytes() -> dict[str, int]:
    """Tayyor path'lar -> bytesReceived, KAMERA bo'yicha jamlangan (sub-path'lar ham)."""
    # SAHIFALAB o'qiladi: path ro'yxati standart holda 100 element beradi va
    # 100 dan ortiq path bo'lganda kameralar HOLATI ham xato aniqlanardi
    # (tayyor oqim "oflayn" bo'lib ko'rinardi) — [[mediamtx.list_paths]].
    try:
        out: dict[str, int] = {}
        for p in await mediamtx.list_paths():
            if not p.get("ready"):
                continue
            base = _base_path(p["name"])
            out[base] = out.get(base, 0) + int(p.get("bytesReceived") or 0)
        return out
    except httpx.HTTPError:
        return {}


async def _stream_activity() -> tuple[set[str], set[str]]:
    """(active, ready) qaytaradi:
      * ready  — MediaMTX'da manba ulangan (publisher bor) path'lar.
      * active — ulardan HAQIQATAN kadr (bayt) kelayotganlari (bytesReceived oshgan).
    ready bo'lib active bo'lmagan = publisher ulangan-u kadr to'xtagan (stalled) →
    online emas. Birinchi ko'rishda (oldingi bayt yo'q) oqayotgan deb olamiz."""
    global _prev_bytes
    now_bytes = await _ready_paths_bytes()
    ready = set(now_bytes.keys())
    active = {
        name for name, b in now_bytes.items()
        if _prev_bytes.get(name) is None or b > _prev_bytes[name]
    }
    _prev_bytes = now_bytes  # ready bo'lmay qolganlar tabiiy ravishda tushib qoladi
    return active, ready


def _snapshot(cam: Camera) -> SimpleNamespace:
    """Kamera ORM obyektidan kerakli maydonlarni plain qiymatga ko'chiradi —
    DB sessiyasi yopilgach (I/O fazasi davomida) xavfsiz foydalanish uchun."""
    return SimpleNamespace(
        id=cam.id,
        stream_path=cam.stream_path,
        status=cam.status,
        last_seen_at=cam.last_seen_at,
        codec=cam.codec,
        transcode=cam.transcode,
        keep_warm=bool(getattr(cam, "keep_warm", False)),
        sub_codec=cam.sub_codec,
        sub_transcode=cam.sub_transcode,
        is_ezviz=cam.is_ezviz,
        is_isup=cam.is_isup,
        isup_device_id=cam.isup_device_id,
        isup_channel=cam.isup_channel,
        ezviz_serial=cam.ezviz_serial,
        ezviz_channel=cam.ezviz_channel,
        ezviz_account_id=cam.ezviz_account_id,
        rtsp_url_enc=cam.rtsp_url_enc,
        rtsp_sub_url_enc=cam.rtsp_sub_url_enc,
    )


class _Plan:
    """Probe natijasi — yoziladigan o'zgarishlar (sessiyasiz hisoblanadi)."""

    def __init__(self) -> None:
        self.online_ids: list = []       # online (last_seen=now, status=online)
        self.offline_ids: list = []      # offline'ga o'tdi
        self.nostream_ids: list = []     # tarmoqda bor-u video bermaydi (nosoz)
        self.codec_updates: list = []    # (cam_id, codec, transcode)
        self.sub_updates: list = []      # (cam_id, sub_codec, sub_transcode)
        self.reupsert: list = []         # (path, rtsp_plain, transcode, always_on) — asosiy YOKI sub
        self.changes: list = []          # (str_id, status) — Redis push uchun

    def mark(self, snap: SimpleNamespace, online: bool | None, now,
             *, fault: str | None = None) -> None:
        """Tolerantlik bilan holatni belgilaydi (online=None -> o'zgartirmaydi).

        `fault` berilsa (masalan "no_stream") kamera "online" deb emas, ALOHIDA
        kategoriya sifatida yoziladi: qurilma tarmoqda bor-u video bermayapti.
        Bu holat oldin "online" ko'rinib, foydalanuvchi bosganda 40s kutardi.
        """
        if online is None:
            return
        if online and fault:
            self.nostream_ids.append(snap.id)
            if snap.status != fault:
                self.changes.append((str(snap.id), fault))
        elif online:
            self.online_ids.append(snap.id)
            if snap.status != "online":
                self.changes.append((str(snap.id), "online"))
        else:
            stale = (
                snap.last_seen_at is None
                or (now - snap.last_seen_at).total_seconds() > settings.offline_after_seconds
            )
            if stale and snap.status != "offline":
                self.offline_ids.append(snap.id)
                self.changes.append((str(snap.id), "offline"))


async def _probe_ezviz(snaps, accs, active, ready, now, plan: _Plan, *, use_cloud: bool = True) -> None:
    """EZVIZ kameralar holati:
      * kadr oqayotgan (active) -> online.
      * publisher ulangan-u kadr to'xtagan (ready, lekin active emas = stalled)
        -> online emas (tolerantlik bilan offline). Bulutga ishonmaymiz — kadr
        kelmayapti, demak biz uzata olmaymiz.
      * path yo'q (on-demand bo'sh, masalan batareyali EZVIZ) -> use_cloud bo'lsa
        bulut qurilma ro'yxatidan aniqlanadi; bo'lmasa o'zgartirilmaydi.

    Tolerantlik RTSP bilan bir xil: bulut javob bermasa holat o'zgarmaydi.
    Sessiyasiz ishlaydi — `accs` oldindan yuklangan hisob snapshot'lari.
    """
    if not snaps:
        return

    # Nosozlik kategoriyasi: publisher aniq chiqish kodi bilan tugagan kameralar
    # ([[manager.note_publisher_exit]]). Bulut "online" deydi-yu, video yo'q.
    fmap = ezviz_manager.faults()
    dev_cache: dict = {}  # account_id -> {serial: dev} yoki None (creds yo'q/xato)

    async def devices_for(acc: SimpleNamespace):
        if acc.id in dev_cache:
            return dev_cache[acc.id]
        result = None
        provider = acc.provider or "ezviz"
        region = acc.region or (
            settings.hik_default_region if provider == "hik" else settings.ezviz_default_region
        )
        try:
            result = await ezviz_tokens.get_devices(
                str(acc.id), acc.account, crypto.decrypt(acc.password_enc), region, provider,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("EZVIZ qurilma ro'yxati xatosi (hisob %s): %s", acc.id, exc)
            result = None
        dev_cache[acc.id] = result
        return result

    for snap in snaps:
        cam_id = str(snap.id)
        if snap.stream_path in active:
            online: bool | None = True            # kadr kelyapti
            if cam_id in fmap:
                ezviz_manager.clear_fault(cam_id)  # kadr keldi -> nosozlik tuzaldi
                fmap.pop(cam_id, None)
        elif snap.stream_path in ready:
            online = False                        # publisher bor-u kadr to'xtagan (stalled)
        elif not use_cloud:
            online = None  # bu siklda bulut so'ralmaydi -> holat o'zgarmaydi
        else:
            acc = accs.get(snap.ezviz_account_id)
            devmap = await devices_for(acc) if acc else None
            if devmap is None:
                online = None  # aniqlab bo'lmadi -> holatni o'zgartirmaymiz
            else:
                dev = devmap.get(snap.ezviz_serial)
                online = bool(dev and dev["status"] == 1)
                # KANAL DARVOZASI: bulut qurilma (NVR) online bo'lsa ham, unda
                # MAVJUD BO'LMAGAN kanal oqim bermaydi. Aks holda kamera "online"
                # ko'rinib, ochilганда 18s "yuklanmoqda"da qotib qolardi.
                # Faqat online'ni offline'ga tushiramiz (aksincha emas), va faqat
                # kesh kanal to'plamini ISHONCHLI bilganda ([[vtm_cache]]).
                if online and vtm_cache.channel_missing(
                        str(snap.ezviz_account_id), snap.ezviz_serial,
                        snap.ezviz_channel or 1):
                    online = False
        # Bulut/ready bo'yicha "online", lekin publisher video bermagan bo'lsa —
        # alohida kategoriya. FAULT_MISSING'ni bu yerda ishlatmaymiz: qurilma
        # bulut ro'yxatidan tushgan bo'lsa yuqoridagi tekshiruv allaqachon
        # offline qiladi.
        fault = None
        if online and fmap.get(cam_id, {}).get("reason") == ezviz_manager.FAULT_NO_STREAM:
            fault = ezviz_manager.FAULT_NO_STREAM
        plan.mark(snap, online, now, fault=fault)


async def probe_once(redis: aioredis.Redis | None = None, *, check_ezviz: bool = True) -> None:
    """Barcha faol kameralarni bir marta tekshirib, holat va kodekni yangilaydi.

    Uch faza: (1) qisqa sessiyada yuklash, (2) sessiyaSIZ I/O (ffprobe + bulut),
    (3) qisqa sessiyada yozish — uzun I/O davomida DB ulanishi band turmasin.
    check_ezviz=False bo'lsa EZVIZ buluti so'ralmaydi (RTSP tez tekshiriladi-yu,
    EZVIZ buluti alohida sekin oraliqda).
    """
    # --- Faza 1: yuklash (qisqa sessiya) ---
    async with SessionLocal() as db:
        cams = (
            (await db.execute(select(Camera).where(Camera.enabled.is_(True))))
            .scalars()
            .all()
        )
        if not cams:
            return
        snaps = [_snapshot(c) for c in cams]
        # EZVIZ hisob snapshot'lari faqat bulut so'raladigan siklda kerak.
        acc_ids = (
            {s.ezviz_account_id for s in snaps if s.is_ezviz and s.ezviz_account_id}
            if check_ezviz else set()
        )
        accs = {}
        if acc_ids:
            for a in (
                await db.execute(select(EzvizAccount).where(EzvizAccount.id.in_(acc_ids)))
            ).scalars():
                accs[a.id] = SimpleNamespace(
                    id=a.id, account=a.account, password_enc=a.password_enc,
                    provider=a.provider, region=a.region,
                )

    # --- Faza 2: I/O (sessiyasiz) ---
    plan = _Plan()
    active, ready = await _stream_activity()
    sem = asyncio.Semaphore(settings.probe_concurrency)
    rtsp_snaps = [s for s in snaps if not s.is_ezviz]
    ezviz_snaps = [s for s in snaps if s.is_ezviz]

    # ISUP: qurilma o'zi ulanadi, shuning uchun "onlayn" = bridge'da ro'yxatdan
    # o'tgan. Asosiy oqim so'ralgandagina ochiq ([[isup]]) — uni ffprobe qilish
    # tomosha qilinmagan har kamerani oflayn ko'rsatardi.
    isup_state = (await isup.bridge_state()) if any(s.is_isup for s in rtsp_snaps) else None

    async def check(snap: SimpleNamespace):
        if snap.stream_path in active:
            info, ok, rtsp = {"codec": None}, True, None  # kadr kelyapti -> online
        elif snap.is_isup:
            ok = isup.device_online(isup_state, snap.isup_device_id)  # None = bilmaymiz
            # Bridge oqim publish qilib turgan bo'lsa qurilma albatta ulangan — API
            # javob bermasa ham (`ready` qurilma darajasida: `isup/<id>`).
            if f"isup/{snap.isup_device_id}" in ready:
                ok = True
            info, rtsp = {"codec": None}, None
            # Kodek noma'lum bo'lsa asosiy oqim ochiq turadi ([[isup.want_lines]]) —
            # shu paytda bir marta aniqlab olamiz.
            if ok and snap.codec == "auto":
                try:
                    rtsp = crypto.decrypt(snap.rtsp_url_enc)
                except Exception:  # noqa: BLE001
                    rtsp = None
                if rtsp:
                    async with sem:
                        info = await probe_info(rtsp, timeout=settings.probe_timeout_seconds) or info
        else:
            try:
                rtsp = crypto.decrypt(snap.rtsp_url_enc)
            except Exception:  # noqa: BLE001
                return snap, None, False, None, None  # ochib bo'lmadi (kalit muammosi)
            async with sem:
                info = await probe_info(rtsp, timeout=settings.probe_timeout_seconds)
            ok = info is not None

        # Sub-stream kodeki BIR MARTA aniqlanadi — `sub_codec` "auto" bo'lganda.
        # Aniqlangach boshqa probe qilinmaydi, shuning uchun 200 kamerali tizimda
        # bu doimiy qo'shimcha yuk EMAS. Kamera erishib bo'lmasa urinmaymiz
        # (keyingi siklda qayta ko'riladi).
        sub_res = None
        if ok and snap.rtsp_sub_url_enc is not None and snap.sub_codec == "auto":
            try:
                sub_rtsp = crypto.decrypt(snap.rtsp_sub_url_enc)
            except Exception:  # noqa: BLE001
                sub_rtsp = None
            if sub_rtsp:
                async with sem:
                    si = await probe_info(sub_rtsp, timeout=settings.probe_timeout_seconds)
                if si and si.get("codec"):
                    sub_res = (si["codec"], sub_rtsp)
        return snap, info, ok, rtsp, sub_res

    results = await asyncio.gather(*(check(s) for s in rtsp_snaps))
    now = datetime.now(timezone.utc)

    await _probe_ezviz(ezviz_snaps, accs, active, ready, now, plan, use_cloud=check_ezviz)

    for snap, info, ok, rtsp, sub_res in results:
        plan.mark(snap, ok, now)
        if ok:
            detected = (info or {}).get("codec")
            if detected and detected != snap.codec:
                transcode = snap.transcode
                if detected == "h265" and not snap.transcode:
                    transcode = True  # brauzerda ijro uchun H.264 ga
                    # rtsp allaqachon deshifrlangan (qayta deshifrlamaymiz)
                    if rtsp is not None:
                        plan.reupsert.append((snap.stream_path, rtsp, True, snap.keep_warm))
                plan.codec_updates.append((snap.id, detected, transcode))
        # Sub-stream: o'z kodeki bo'yicha MUSTAQIL hal qilinadi. Ikkinchi profil
        # deyarli doim H.264 -> transkodsiz to'g'ridan tortiladi (CPU/GPU ~0).
        # Faqat sub'ning O'ZI HEVC bo'lsa transkod yoqiladi.
        if sub_res is not None:
            sub_codec, sub_rtsp = sub_res
            sub_transcode = sub_codec == "h265"
            plan.sub_updates.append((snap.id, sub_codec, sub_transcode))
            if sub_transcode != snap.sub_transcode:
                plan.reupsert.append((f"{snap.stream_path}/sub", sub_rtsp, sub_transcode, False))

    # --- Faza 3: yozish (qisqa sessiya, bulk) ---
    if (plan.online_ids or plan.offline_ids or plan.nostream_ids
            or plan.codec_updates or plan.sub_updates):
        async with SessionLocal() as db:
            if plan.online_ids:
                await db.execute(
                    update(Camera).where(Camera.id.in_(plan.online_ids))
                    .values(status="online", last_seen_at=now)
                )
            if plan.nostream_ids:
                # last_seen_at TEGILMAYDI — kadr kelmadi, ya'ni "ko'rildi" emas.
                await db.execute(
                    update(Camera).where(Camera.id.in_(plan.nostream_ids))
                    .values(status="no_stream")
                )
            if plan.offline_ids:
                await db.execute(
                    update(Camera).where(Camera.id.in_(plan.offline_ids)).values(status="offline")
                )
            for cid, codec, transcode in plan.codec_updates:
                await db.execute(
                    update(Camera).where(Camera.id == cid).values(codec=codec, transcode=transcode)
                )
            for cid, sub_codec, sub_transcode in plan.sub_updates:
                await db.execute(
                    update(Camera).where(Camera.id == cid)
                    .values(sub_codec=sub_codec, sub_transcode=sub_transcode)
                )
            await db.commit()

    # Kodek aniqlangach MediaMTX path'ini qayta sozlash (asosiy va/yoki sub)
    for path_name, rtsp, transcode, always_on in plan.reupsert:
        await mediamtx.safe_upsert(path_name, rtsp, transcode, always_on)

    # Holat o'zgarishlarini e'lon qilish (iste'molchilar uchun)
    if redis and plan.changes:
        for cam_id, st in plan.changes:
            try:
                await redis.publish(STATUS_CHANNEL, json.dumps({"camera_id": cam_id, "status": st}))
            except Exception as exc:  # noqa: BLE001 — transient Redis uzilishi push'ni yo'qotmasin-u, sikl yiqilmasin
                log.warning("Status push xatosi (%s): %s", cam_id, exc)
    # Dashboard "So'nggi hodisalar" jurnali ([[stats.record_events]])
    await stats.record_events(redis, plan.changes)
    if plan.changes:
        log.info("Holat o'zgardi: %s", plan.changes)
    if plan.sub_updates:
        log.info("Sub-stream kodeki aniqlandi: %s", plan.sub_updates)
    if plan.reupsert:
        log.info("Kodek o'zgarishi — MediaMTX path qayta sozlandi: %d", len(plan.reupsert))


async def run_probe_loop(redis: aioredis.Redis | None = None) -> None:
    """Doimiy probe sikli (lifespan'da background task sifatida ishlaydi).

    RTSP har `probe_interval_seconds`da; EZVIZ buluti esa kamida
    `ezviz_status_interval_seconds`da bir marta (rate-limit/batareya tejash)."""
    log.info(
        "Probe sikli ishga tushdi (interval=%ss, EZVIZ bulut=%ss)",
        settings.probe_interval_seconds, settings.ezviz_status_interval_seconds,
    )
    last_ezviz = 0.0
    while True:
        try:
            now = time.monotonic()
            check_ezviz = (now - last_ezviz) >= settings.ezviz_status_interval_seconds
            if check_ezviz:
                last_ezviz = now
            await probe_once(redis, check_ezviz=check_ezviz)
            await stats.tick(redis)  # yurak urishi + onlayn soni tarixi
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Probe siklida xato: %s", exc)
        await asyncio.sleep(settings.probe_interval_seconds)
