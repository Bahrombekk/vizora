"""EZVIZ publisher'larni on-demand boshqaruvchi supervisor.

Tomoshabin (MediaMTX reader) paydo bo'lganda kamera uchun publisher subprocess
ishga tushadi (bulut -> H.264 -> MediaMTX). Reader bo'lmasa idle-reaper uni
to'xtatadi — shu bilan batareya/4G tejaladi (RTSP sourceOnDemand'ga o'xshash).

Foydalanish:
  * ensure_for_camera(db, cam)  — tomoshabin so'raganda chaqiriladi (oqimni boshlaydi)
  * run_supervisor()            — lifespan'da fon vazifasi (reaper + token refresh)
  * run_always_warm()           — tanlangan kameralarni 24/7 tayyor tutadi
  * stop_all()                  — to'xtashda
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.models.camera import Camera
from vizora.models.ezviz_account import EzvizAccount
from vizora.core import crypto
from vizora.media import ffmpeg_hw, mediamtx, signals
from vizora.sources.ezviz import tokens

log = logging.getLogger("vizora.ezviz.manager")


class EzvizConfigError(RuntimeError):
    """EZVIZ hisob sozlanmagan yoki kamera noto'g'ri."""


class _Publisher:
    __slots__ = ("proc", "stream_path", "last_request", "provider", "gpu", "raw_only",
                 "substream")

    def __init__(self, proc: asyncio.subprocess.Process, stream_path: str,
                 provider: str = "ezviz", gpu: bool = True, raw_only: bool = False,
                 substream: bool = False):
        self.proc = proc
        self.stream_path = stream_path
        self.last_request = time.monotonic()
        self.provider = provider  # idle-reaper provayderga qarab vaqtni tanlaydi
        self.gpu = gpu            # NVENC bilan boshlanganmi (sessiya limitini hisoblash uchun)
        # Faqat <path>/raw chiqishi bilan ishlayaptimi (enkodersiz, CPU ~0).
        # H.264 kerak bo‘lib qolsa (HLS/WebRTC/iste‘molchi) publisher qayta boshlanadi.
        self.raw_only = raw_only
        # Bulutdan KICHIK (substream) oqim so'ralganmi. Devor plitkalari shuni
        # oladi: D1 ~0.1 MP, ya'ni 3200x1800 (5.76 MP) dan ~57 barobar kichik —
        # brauzer o'nlab plitkani ko'tara oladi. To'liq ekran esa asosiy oqimni
        # so'raydi va publisher qayta boshlanadi (pastga tushirish YO'Q).
        self.substream = substream

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None


# camera_id (str) -> _Publisher
_publishers: dict[str, _Publisher] = {}
_start_lock = asyncio.Lock()
# camera_id (str) -> oxirgi HAQIQIY ko'rish vaqti (monotonic). Warm-keeper qaysi
# kameralarni issiq tutishni shu bo'yicha tartiblaydi (so'nggi ko'rilganga ustunlik).
_last_view: dict[str, float] = {}


def _publish_url(stream_path: str) -> str:
    return mediamtx.rtsp_url(stream_path)


async def _resolve_account(db: AsyncSession, cam: Camera) -> EzvizAccount:
    if not cam.ezviz_account_id:
        raise EzvizConfigError("Kameraga EZVIZ hisobi biriktirilmagan")
    acc = await db.get(EzvizAccount, cam.ezviz_account_id)
    if acc is None:
        raise EzvizConfigError("EZVIZ hisobi topilmadi")
    return acc


async def ensure_for_camera(db: AsyncSession, cam: Camera, *, viewer: bool = False,
                            need_h264: bool = True, want_substream: bool = False) -> None:
    """Tomoshabin so'raganda EZVIZ kamera oqimini boshlaydi (idempotent).

    Allaqachon ishlayotgan bo'lsa faqat 'last_request' yangilanadi.
    `viewer=True` — HAQIQIY ko'rish (warm-keeper emas): oxirgi ko'rish vaqtini
    belgilaydi (adaptiv warm-keeper ustunlik bersin).
    """
    if not cam.is_ezviz:
        return
    if not cam.ezviz_serial:
        raise EzvizConfigError("Kamera EZVIZ seriya raqami yo'q")

    if viewer:
        _last_view[str(cam.id)] = time.monotonic()

    acc = await _resolve_account(db, cam)
    account = acc.account
    provider = acc.provider or "ezviz"
    region = acc.region or (
        settings.hik_default_region if provider == "hik" else settings.ezviz_default_region
    )
    # Maxfiy maydonlarni deshifrlash — AES_KEY_HEX almashtirilgan/buzilgan bo'lsa
    # InvalidTag chiqadi; uni xom 500/WS xatosi sifatida emas, EzvizConfigError
    # sifatida ko'taramiz (chaqiruvchilar buni 400 sifatida ishlov beradi).
    try:
        password = crypto.decrypt(acc.password_enc)
        verify_code = (
            crypto.decrypt(cam.ezviz_verify_code_enc) if cam.ezviz_verify_code_enc else None
        )
    except Exception as exc:  # noqa: BLE001 — crypto kutubxonasi turli xato turlari
        raise EzvizConfigError(
            "EZVIZ maxfiy ma'lumotlarini deshifrlab bo'lmadi (AES kalit mosmi?)"
        ) from exc

    cam_id = str(cam.id)
    # XOM rejim FAQAT transkodsiz chiqish beradi — `<path>/raw`. Panel pleyeri
    # (WS + WebCodecs) uchun shu yetarli va CPU ~0. `drop_corrupt` kameralarda
    # passthrough mumkin emas (oqim PyAV bilan dekodlanadi), shuning uchun ular
    # doim to'liq rejimda ishlaydi.
    raw_only = (not need_h264) and settings.ezviz_raw_path \
        and not getattr(cam, "ezviz_drop_corrupt", False)
    # Substream: DB bayrog'i yoki so'rov (devor plitkasi) tanlaydi.
    substream = bool(getattr(cam, "ezviz_use_substream", False) or want_substream)
    async with _start_lock:
        pub = _publishers.get(cam_id)
        # O'lgan publisher ustiga yangisi yozilishidan OLDIN chiqish kodini
        # nosozlik kategoriyasiga yozamiz. Aks holda foydalanuvchi darrov qayta
        # bosganda idle-reaper ko'rishga ulgurmasdi va sabab yo'qolib ketardi.
        if pub is not None and not pub.alive:
            note_publisher_exit(cam_id, pub.proc.returncode)
        # Substream'dan ASOSIY oqimga ko'tarilish (to'liq ekran ochilganda) —
        # qayta boshlash. Teskarisi (asosiydan substream'ga) QILINMAYDI: kimning
        # to'liq ekrani ochiq bo'lsa, devor plitkasi uni buzmasligi kerak.
        if pub and pub.alive and pub.substream and not substream:
            log.info("Substream'dan asosiy oqimga o'tamiz: %s", cam.stream_path)
            pub.proc.terminate()
            _publishers.pop(cam_id, None)
            pub = None
        elif pub and pub.alive and not pub.substream and substream:
            substream = False   # allaqachon asosiy oqim ishlayapti — shuni ishlatamiz
        if pub and pub.alive and pub.raw_only and need_h264:
            # Panel uchun XOM rejimda ishlayotgan edi, endi H.264 kerak bo'ldi
            # (HLS / WebRTC / tashqi iste'molchi). Enkoderlarni qo'shishning
            # yagona yo'li — publisher'ni qayta boshlash (~2-3s bir marta).
            log.info("XOM rejimdan to'liq transkodga o'tamiz: %s", cam.stream_path)
            pub.proc.terminate()
            _publishers.pop(cam_id, None)
            pub = None
        if pub and pub.alive:
            pub.last_request = time.monotonic()
            return

        # ABR uchun SD rendition "<path>/sub" ga publish qilinadi — MediaMTX path'i
        # oldindan tayyor turishi kerak (asosiy path'ni chaqiruvchi yaratadi).
        await mediamtx.safe_ensure_path(f"{cam.stream_path}/sub")
        # XOM (transkodsiz) path — panel pleyeri shundan o'qiydi ([[publish._raw_output]])
        if settings.ezviz_raw_path:
            await mediamtx.safe_ensure_path(f"{cam.stream_path}/raw")

        # Token faylini kafolatlaymiz (publisher subprocess shundan o'qiydi).
        # Kalit sifatida hisob id'sini ishlatamiz (bir hisob = bir sessiya).
        token_file = await tokens.ensure_token_file(str(acc.id), account, password, region, provider)

        cmd = [
            sys.executable, "-m", "vizora.sources.ezviz.publish",
            "--serial", cam.ezviz_serial,
            "--channel", str(cam.ezviz_channel or 1),
            "--token-file", token_file,
            "--publish-url", _publish_url(cam.stream_path),
            "--provider", provider,
        ]
        # Verify code argv'da EMAS — `ps` ro'yxatida hammaga ko'rinardi (audit 2026-11-03).
        # Env orqali; publisher uni o'qigach o'chiradi (ffmpeg bolalariga o'tmaydi).
        if getattr(cam, "ezviz_drop_corrupt", False):
            cmd += ["--drop-corrupt"]
        if substream:
            cmd += ["--substream"]
        if raw_only:
            cmd += ["--raw-only"]

        # Publisher chiqishini per-kamera log fayliga yozamiz (diagnostika uchun).
        import os
        os.makedirs(settings.ezviz_token_dir, exist_ok=True)
        logf = open(os.path.join(settings.ezviz_token_dir, f"pub-{cam_id}.log"), "wb")
        # GeForce NVENC sessiya chegarasi: GPU'dagi faol publisher'lar limitga yetsa
        # yangisi CPU'da (FFMPEG_HWACCEL=none) boshlanadi — sekinroq, lekin ISHLAYDI.
        # Aks holda ffmpeg "OpenEncodeSessionEx failed (21)" bilan o'lib, kamera
        # umuman ochilmasdi. Publisher o'zi ham zaxira sifatida shu xatoni ko'rib
        # CPU'ga o'tadi ([[ffmpeg_hw.note_encoder_death]]).
        # XOM rejimda enkoder umuman ochilmaydi — NVENC sessiyasi sarflanmaydi.
        gpu = False if raw_only else _gpu_slot_free()
        env = None
        if raw_only:
            pass
        elif not gpu:
            env = {**os.environ, "FFMPEG_HWACCEL": "none"}
            log.info("NVENC sessiya chegarasi (%d faol GPU publisher) — %s CPU (libx264) da boshlanadi",
                     _gpu_publishers(), cam.stream_path)
        if verify_code:
            env = {**(env or os.environ), "VZ_VERIFY_CODE": verify_code}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=logf,
            stderr=logf,
            env=env,
        )
        pub = _Publisher(proc, cam.stream_path, provider, gpu=gpu,
                         raw_only=raw_only, substream=substream)
        _publishers[cam_id] = pub
        await signals.publisher_started(cam.stream_path)
        asyncio.create_task(_watch_exit(cam_id, pub), name=f"pubwatch:{cam_id}")
        log.info("EZVIZ publisher boshlandi: %s (serial=%s, provayder=%s, dekod=%s, "
                 "gpu=%s, rejim=%s, oqim=%s)",
                 cam.stream_path, cam.ezviz_serial, provider, bool(verify_code), gpu,
                 "XOM" if raw_only else "to'liq", "SUB" if substream else "asosiy")


# ===== Nosozlik kategoriyasi =====
# camera_id -> {"reason": str, "since": float, "checked": float, "tries": int}
# "online, lekin ochilmaydi" kameralarni KO'RINADIGAN qilish uchun: publisher
# aniq chiqish kodi bilan tugaydi ([[publish.EXIT_NO_STREAM]]), biz shuni eslab
# qolamiz, `probe` esa kamerani `no_stream` kategoriyasiga yozadi va
# `run_fault_checker` vaqti-vaqti bilan qayta tekshirib turadi.
_faults: dict[str, dict] = {}

FAULT_NO_STREAM = "no_stream"    # bulutda bor, video bermaydi
FAULT_MISSING = "missing"        # bulut ro'yxatida yo'q (oflayn)
_EXIT_FAULTS = {3: FAULT_NO_STREAM, 4: FAULT_MISSING}
_FAULT_CHECK_BATCH = 2        # bir siklda nechta kamerani qayta sinaymiz
_FAULT_CHECK_TIMEOUT = 25.0   # har biri uchun kutish (bulut sovuq start ~6-20s)


def note_publisher_exit(cam_id: str, code: int | None) -> None:
    """Publisher tugadi — chiqish kodi nosozlikni bildirsa, eslab qolamiz."""
    reason = _EXIT_FAULTS.get(code or 0)
    if reason is None:
        return
    f = _faults.get(cam_id)
    now = time.monotonic()
    if f and f["reason"] == reason:
        f["tries"] += 1
        f["checked"] = now
    else:
        _faults[cam_id] = {"reason": reason, "since": now, "checked": now, "tries": 1}
    log.warning("Kamera nosoz deb belgilandi: %s (%s)", cam_id, reason)


def clear_fault(cam_id: str) -> None:
    """Kadr keldi — kamera sog'lom, kategoriyadan chiqaramiz."""
    if _faults.pop(cam_id, None) is not None:
        log.info("Kamera nosozligi tuzaldi: %s", cam_id)


def faults() -> dict[str, dict]:
    """Nosoz kameralar: camera_id -> {reason, since_s, tries}."""
    now = time.monotonic()
    return {
        cid: {"reason": f["reason"], "since_s": round(now - f["since"], 1), "tries": f["tries"]}
        for cid, f in _faults.items()
    }


def _gpu_publishers() -> int:
    """Hozir NVENC bilan ishlayotgan (tirik) publisher'lar soni."""
    return sum(1 for p in _publishers.values() if p.alive and p.gpu)


def _gpu_slot_free() -> bool:
    """Yangi publisher GPU'da boshlansa bo'ladimi (NVENC bor va limit oshmagan)?"""
    if not ffmpeg_hw.use_nvenc() or ffmpeg_hw.gpu_disabled():
        return False
    return _gpu_publishers() < settings.nvenc_max_sessions


def publisher_dead(stream_path: str) -> bool:
    """Shu path uchun publisher BOR, lekin jarayoni tugagan bo'lsa True.

    RTSP kamerada publisher umuman bo'lmaydi (MediaMTX o'zi tortadi) — u holda
    False, ya'ni chaqiruvchi kutishni davom ettiradi. [[ws_stream]] shu orqali
    OFLAYN kamerada 90s bekor kutmaydi: publish.py qurilmani bulut ro'yxatida
    topmasa ~1s da chiqadi."""
    return _publisher_dead(stream_path)


def raw_only_running(camera_id: str) -> bool:
    """Kamera uchun XOM (enkodersiz) rejimdagi publisher tirik — H.264 so'ralsa uni
    qayta boshlash kerak bo'ladi (~4.6s), shuning uchun panel xom yo'lda qoladi."""
    pub = _publishers.get(camera_id)
    return bool(pub and pub.alive and pub.raw_only)


def touch_viewer(stream_path: str) -> None:
    """Tomoshabin oqimni KUTMOQDA (WS hub) — publisher'ning idle soatini yangilaydi.

    Path tayyor bo'lmaguncha MediaMTX'da reader bo'lmaydi, idle-reaper esa
    'reader yo'q' deb publisher'ni bulutga ulanayotgan paytida o'ldirardi."""
    for pub in _publishers.values():
        if pub.stream_path == stream_path:
            pub.last_request = time.monotonic()
            return


def _publisher_dead(stream_path: str) -> bool:
    """Shu path uchun publisher jarayoni tugagan (o'lgan)mi?

    `_publishers` kamera id bo'yicha kalitlangan, bizda esa path bor — shuning
    uchun path bo'yicha izlaymiz. Publisher YO'Q bo'lsa False qaytaramiz: RTSP
    kameralarda publisher umuman bo'lmaydi (MediaMTX o'zi tortadi), ularni
    "o'lgan" deb hisoblab kutishni buzib qo'ymaymiz."""
    for pub in _publishers.values():
        if pub.stream_path == stream_path:
            return pub.proc.returncode is not None
    return False


async def _path_ready(stream_path: str) -> bool:
    """MediaMTX'da path 'ready' (publisher ulanib, track'lar tayyor)mi?"""
    # BITTA path so'raladi. Avval butun path ro'yxati tortilardi, u esa
    # SAHIFALANADI: 100 dan ortiq path bo'lganda kamera ro'yxatga tushmay,
    # `wait_ready` har safar 18s chegarasiga urilardi ([[mediamtx.list_paths]]).
    try:
        p = await mediamtx.get_path(stream_path)
        return bool(p and p.get("ready"))
    except httpx.HTTPError:
        return False


async def wait_ready(stream_path: str, timeout: float = 18.0, *,
                     warm_hls: bool = True) -> bool:
    """Publisher bulutdan ulanib MediaMTX'ga publish qilguncha kutadi (~8-10s).

    EZVIZ kamera on-demand: ensure_for_camera publisher'ni boshlaydi, lekin oqim
    darhol tayyor bo'lmaydi. Tomoshabin (hls.js) birinchi manifest so'rovida 404
    olmasligi va publisher reader'siz idle-reaper'ga tushib qolmasligi uchun shu
    yerda kutamiz. Tayyor bo'lsa True; timeout bo'lsa False (frontend baribir
    qayta uradi)."""
    t0 = time.monotonic()
    deadline = t0 + timeout
    while time.monotonic() < deadline:
        # Publisher O'LGAN bo'lsa kutishning ma'nosi yo'q. Bu ayniqsa OFLAYN
        # kamerada muhim: publish.py qurilmani bulut ro'yxatida topmasa chiqadi,
        # bu ~1s da bo'ladi, biz esa avval baribir 18s (timeout) kutib turardik —
        # foydalanuvchi 18s "yuklanmoqda" ko'rib, keyin hech narsa ko'rmasdi.
        if _publisher_dead(stream_path):
            log.info("[vaqt] publisher o'lgan — kutish to'xtatildi %.2fs (%s)",
                     time.monotonic() - t0, stream_path)
            return False
        if await _path_ready(stream_path):
            log.info("[vaqt] MediaMTX path ready %.2fs (%s)",
                     time.monotonic() - t0, stream_path)
            # Path tayyor, lekin HLS MUXER hali yo'q — brauzer ulanganda u noldan
            # tug'ilardi (~2-4s) va ustiga 2 segment kutilardi. Shu kutishni
            # PARALLEL bajaramiz: muxerni hoziroq isitib, playlist'da yetarli
            # segment paydo bo'lishini kutamiz. Brauzer TAYYOR playlist'ga tushadi.
            # WebRTC'da segment KERAK EMAS (birinchi kadr darhol uzatiladi) —
            # bu ~2s kutishni butunlay o'tkazib yuboramiz.
            if warm_hls:
                t1 = time.monotonic()
                ok = await _wait_hls_segments(
                    stream_path,
                    timeout=min(6.0, max(1.0, deadline - time.monotonic())))
                log.info("[vaqt] HLS %d segment %.2fs (jami %.2fs, %s)",
                         2 if ok else 0, time.monotonic() - t1,
                         time.monotonic() - t0, stream_path)
            return True
        # Qadam kichik: bu SOF kutish kechikishi (o'rtacha yarim qadam bekorga
        # ketadi). MediaMTX API chaqiruvi lokal va arzon.
        await asyncio.sleep(0.15)
    return False


def _playable_count(txt: str) -> tuple[int, bool]:
    """Playlist'da NECHTA o'ynatiladigan bo'lak bor: (soni, ll_hls_mi).

    LL-HLS'da MediaMTX playlistni boshida `#EXT-X-GAP` + `#EXTINF` bilan
    to'ldiradi (oyna to'lgan ko'rinadi), shuning uchun oddiy `#EXTINF` sanog'i
    ALDAYDI — tayyor bo'lmagan oqimni "tayyor" deb ko'rsatardi. LL-HLS'da haqiqiy
    signal `#EXT-X-PART`, mpegts'da esa GAP'siz `#EXTINF`."""
    parts = txt.count("#EXT-X-PART:")
    if parts:
        return parts, True
    lines = txt.splitlines()
    real = sum(1 for i, l in enumerate(lines)
               if l.startswith("#EXTINF")
               and not (i and lines[i - 1].startswith("#EXT-X-GAP")))
    return real, False


async def _wait_hls_segments(stream_path: str, *, timeout: float = 6.0,
                             want: int = 2) -> bool:
    """HLS muxerni isitib, playlist'da `want` ta segment paydo bo'lishini kutadi.

    Nega: MediaMTX HLS muxeri RTSP path'dan MUSTAQIL — u faqat birinchi HLS
    so'rovda tug'iladi. Avval `/stream` faqat path `ready` bo'lishini kutardi,
    keyin brauzer muxer sovuq startini VA 2 segmentni yana kutardi (ketma-ket).
    Endi ikkinchi qismini shu yerda, publisher isishi bilan bir vaqtda bajaramiz."""
    base = settings.mediamtx_hls_url
    deadline = time.monotonic() + timeout
    # SD (sub) muxerini ham parallel isitamiz — hls.js past tarmoqda darhol o'sha
    # variantga o'tishi mumkin, sovuq muxer esa 401/404 beradi.
    sub_task = asyncio.create_task(_warm_one(f"{stream_path}/sub"))
    try:
        # Sessiya cookie'si kerak (MediaMTX cookieCheck) — bitta klient jarini
        # ishlatamiz: index.m3u8 cookie o'rnatadi, variant so'rovi shu bilan ketadi.
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=15.0,
                                     follow_redirects=True) as c:
            # index.m3u8 ni BIR MARTA so'raymiz: MediaMTX har so'rovda YANGI HLS
            # sessiya yaratadi, shuning uchun uni siklda urish sessiya to'playdi
            # (lokal sinovda sekundiga ~5 ta). Muxer sovuq bo'lsa bu so'rov o'zi
            # segment tayyor bo'lguncha kutib turadi.
            var = None
            while var is None and time.monotonic() < deadline:
                try:
                    r = await c.get(f"{base}/{stream_path}/index.m3u8")
                    if r.status_code == 200:
                        var = _first_variant(r.text)  # `?session=` bilan
                except httpx.HTTPError:
                    await asyncio.sleep(0.3)
            if var is None:
                return False
            # Endi FAQAT variant playlist'ni so'raymiz (sessiya o'sha-o'sha).
            while time.monotonic() < deadline:
                try:
                    m = await c.get(f"{base}/{stream_path}/{var}")
                    if m.status_code == 200:
                        n, ll = _playable_count(m.text)
                        # LL-HLS: 3 part (~0.6s) yetarli — pleyer shundan boshlaydi.
                        # mpegts: 2 to'liq segment (~2s) kerak.
                        if n >= (3 if ll else want):
                            return True
                    if m.status_code == 401:
                        return False   # sessiya o'ldi — hls_proxy o'zi yangisini oladi
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
    finally:
        sub_task.cancel()
    return False


async def _warm_one(stream_path: str) -> None:
    """Bitta HLS muxerni isitadi (xatolarga e'tibor bermaydi)."""
    try:
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=8.0,
                                     follow_redirects=True) as c:
            await c.get(f"{settings.mediamtx_hls_url}/{stream_path}/index.m3u8")
    except Exception:  # noqa: BLE001 — "best effort" isitish
        pass


def _first_variant(master: str) -> str | None:
    """Master playlist'dan birinchi media playlist nomini oladi (`?session=` bilan)."""
    for line in master.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ".m3u8" in line:
            return line
    return None


async def _watch_exit(cam_id: str, pub: _Publisher) -> None:
    """Publisher KUTILMAGANDA tugasa (hali ro'yxatda) — media jarayoniga signal.

    WS hub alohida jarayonda: `_publishers` ni ko'rmaydi, shuning uchun oflayn
    kamerani darhol aniqlash ([[publisher_dead]]) Redis orqali ([[signals]]).
    Ataylab to'xtatilgan (`stop`) publisher ro'yxatdan oldin olib tashlanadi —
    u "o'lgan" deb belgilanmaydi."""
    try:
        await pub.proc.wait()
    except Exception:  # noqa: BLE001
        return
    if _publishers.get(cam_id) is pub:
        await signals.publisher_exited(pub.stream_path)


async def stop(camera_id: str) -> None:
    pub = _publishers.pop(camera_id, None)
    if pub is None:
        return
    if pub.alive:
        pub.proc.terminate()
        try:
            await asyncio.wait_for(pub.proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pub.proc.kill()
    log.info("EZVIZ publisher to'xtatildi: %s", pub.stream_path)


async def stop_all() -> None:
    for cam_id in list(_publishers):
        await stop(cam_id)


async def _path_stats() -> dict[str, tuple[bool, int]]:
    """Har path uchun (ready, bytesReceived) — oqim HAQIQATAN oqayotganini bilish uchun.

    `ready` yetarli EMAS: bulut uzilganda ffmpeg RTSP publish'ni ochiq ushlab turadi,
    path `ready` qolaveradi-yu yangi kadr KELMAYDI. bytesReceived o'smasa — o'lik.
    """
    try:
        return {
            p["name"]: (bool(p.get("ready")), int(p.get("bytesReceived") or 0))
            for p in await mediamtx.list_paths()      # SAHIFALAB o'qiladi
        }
    except httpx.HTTPError:
        return {}


async def _path_readers() -> dict[str, int]:
    """MediaMTX'dagi har path uchun reader (tomoshabin) soni."""
    try:
        return {p["name"]: len(p.get("readers") or [])
                for p in await mediamtx.list_paths()}   # SAHIFALAB o'qiladi
    except httpx.HTTPError:
        return {}


async def _warm_hls(stream_path: str) -> None:
    """HLS muxerni ISSIQ tutadi — MediaMTX'dan index.m3u8 ni o'qiydi (HD + SD).

    Publisher (RTSP manba) issiq bo'lsa ham, HLS muxer alohida — u faqat birinchi
    HLS reader kelganda noldan tug'iladi (~4s sovuq start) va manba endigina
    barqarorlashayotgani uchun bir marta RESET bo'ladi (segment hash o'zgaradi) →
    hls.js chalkashib 0:00 da qotadi. Buni davriy index.m3u8 o'qib oldini olamiz:
    muxer bir marta barqaror qurilib turadi, browser ulanganda tayyor muxerga
    tushadi (sovuq start ham, reset ham yo'q). Yengil — segment yuklamaymiz."""
    await _warm_one(stream_path)
    await _warm_one(f"{stream_path}/sub")  # SD bo'lmasligi mumkin — zararsiz


def _readers_for(readers: dict[str, int], stream_path: str) -> int:
    """Kameraning HAMMA path'laridagi tomoshabinlar yig'indisi: asosiy + `/sub`
    (ABR) + `/raw` (WS pleyeri). Bittasini unutish = ko'rilayotgan kamerani
    idle-reaper o'ldirishi."""
    prefix = stream_path + "/"
    return sum(n for p, n in readers.items() if p == stream_path or p.startswith(prefix))


async def _reap_once() -> None:
    """Reader'siz qolib idle vaqtdan oshgan publisher'larni to'xtatadi."""
    if not _publishers:
        return
    readers = await _path_readers()
    now = time.monotonic()
    always = _always_ids()
    for cam_id, pub in list(_publishers.items()):
        if not pub.alive:
            _publishers.pop(cam_id, None)
            log.warning("EZVIZ publisher kutilmaganda to'xtagan: %s (rc=%s)",
                        pub.stream_path, pub.proc.returncode)
            note_publisher_exit(cam_id, pub.proc.returncode)
            continue
        if cam_id in always:
            # DOIM TAYYOR ro'yxati — tomoshabin bo'lmasa ham to'xtatmaymiz.
            pub.last_request = now
            continue
        # Tomoshabin ASOSIY path'da bo'lishi SHART EMAS: WS pleyeri `<path>/raw`
        # dan, ABR esa `<path>/sub` dan o'qiydi. Faqat asosiysini sanasak,
        # /raw orqali ko'rib turgan odamning kamerasi idle deb O'LDIRILARDI.
        if _readers_for(readers, pub.stream_path) > 0 or await signals.viewer_recent(pub.stream_path):
            # tomoshabin bor (MediaMTX reader) yoki media jarayonidagi WS hub manbani
            # kutmoqda ([[signals.touch_viewer]]) — soatni yangilaymiz
            pub.last_request = now
        else:
            # Hik (simli NVR) batareyali emas -> uzoqroq issiq tutiladi (qayta
            # ko'rish bir zumda, ochish/yopish churn'i kam). Batareyali EZVIZ esa
            # tez to'xtaydi (tejamkor).
            idle = settings.hik_idle_seconds if pub.provider == "hik" else settings.ezviz_idle_seconds
            if not pub.raw_only:
                # Enkoder ishlayapti (~1 yadro) — tomoshabinsiz uzoq ushlab turmaymiz.
                idle = min(idle, settings.ezviz_transcode_idle_seconds)
            if now - pub.last_request > idle:
                await stop(cam_id)


def _read_cpu() -> tuple[int, int]:
    """/proc/stat birinchi qatoridan (total, idle) jiffies — tizim (host) CPU."""
    with open("/proc/stat", encoding="ascii") as f:
        vals = [int(x) for x in f.readline().split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return sum(vals), idle


async def _cpu_percent(sample: float = 1.0) -> float:
    """Tizim CPU bandligi (%) — sample sekundlik oraliqda. O'qib bo'lmasa 0.0
    (cheklamaymiz -> warm-keeper hammasini issiq tutadi, eski xatti-harakat)."""
    try:
        t1, i1 = _read_cpu()
        await asyncio.sleep(sample)
        t2, i2 = _read_cpu()
        dt = t2 - t1
        if dt <= 0:
            return 0.0
        return max(0.0, min(100.0, (1.0 - (i2 - i1) / dt) * 100.0))
    except Exception:  # noqa: BLE001
        return 0.0


# Adaptiv "issiq" byudjet: ayni ko'rilmayotgan, lekin oldindan issiq tutiladigan
# kameralar soni. CPU'ga qarab o'sadi/kamayadi (modul holati — sikllar orasida saqlanadi).
_warm_budget = 0
# Warm-keeper oxirgi o'lchagan tizim CPU bandligi (%) — monitoring uchun.
_last_cpu: float = 0.0


def warm_snapshot() -> dict:
    """Warm-keeper jonli holati (monitoring uchun): CPU, byudjet va ayni vaqtda
    "issiq" (pipeline ishlab turgan, bir zumda ochiladigan) kameralar ro'yxati.
    Issiq = tirik publisher (warm-keeper tutgani yoki tomoshabin boshlagani)."""
    import os
    alive = {cid: p for cid, p in _publishers.items() if p.alive}
    return {
        "cpu": round(_last_cpu, 1),
        "budget": _warm_budget,
        "cores": os.cpu_count() or 0,
        "stream_paths": {cid: p.stream_path for cid, p in alive.items()},
    }


async def run_warm_keeper() -> None:
    """Hik kameralarni CPU'ga QARAB adaptiv "issiq" tutadi — ko'rish bir zumda
    bo'lsin, ammo server ortiqcha yuklanmasin.

    Har siklda: tizim CPU'si o'lchanadi -> band bo'lsa issiq sonini kamaytiradi
    (so'nggi ko'rilgan/ayni ko'rilayotganlarga ustunlik), bo'sh bo'lsa yana bittani
    issiq qiladi. Ayni ko'rilayotgan (MediaMTX reader) kameralar HAR DOIM issiq.
    Batareyali EZVIZ bunga kirmaydi (on-demand). Integratsiya API'si o'zgarmaydi.
    """
    if not settings.hik_keep_warm:
        return
    global _warm_budget, _last_cpu
    from sqlalchemy import select

    from vizora.core.db import SessionLocal

    log.info("Hik adaptiv warm-keeper ishga tushdi (CPU oraliq %.0f-%.0f%%)",
             settings.hik_warm_min_cpu_percent, settings.hik_warm_max_cpu_percent)
    while True:
        try:
            cpu = await _cpu_percent(1.0)
            _last_cpu = cpu  # monitoring uchun saqlaymiz
            # Byudjetni CPU'ga qarab bir qadam rostlash (sekin -> tebranish kam)
            if cpu > settings.hik_warm_max_cpu_percent:
                _warm_budget = max(0, _warm_budget - 1)
            elif cpu < settings.hik_warm_min_cpu_percent:
                _warm_budget += 1

            async with SessionLocal() as db:
                cams = (
                    await db.execute(
                        select(Camera)
                        .join(EzvizAccount, EzvizAccount.id == Camera.ezviz_account_id)
                        .where(
                            Camera.enabled.is_(True),
                            Camera.source_type == "ezviz",
                            EzvizAccount.provider == "hik",
                        )
                    )
                ).scalars().all()
                # Byudjet QAT'IY shift bilan chegaralanadi — aks holda CPU past
                # turgan ekan har siklda +1 o'sib, 200 kamerali tizimda 200 ta
                # publisher tomon ketardi.
                _warm_budget = max(
                    settings.hik_warm_min_cameras,
                    min(_warm_budget, settings.hik_warm_max_cameras, len(cams)),
                )

                readers = await _path_readers()
                # Tartib: avval SO'NGGI KO'RILGANLAR (eng yangisi birinchi), keyin
                # hech qachon ko'rilmaganlar nom bo'yicha. Byudjet cheklangani uchun
                # uni aynan qayta ochilishi EHTIMOLI yuqori kameralarga sarflaymiz —
                # amalda operator bir necha kamerani aylanib ko'radi, o'shalari issiq
                # qoladi. (Ilgari tartib faqat alifbo edi: `_last_view` yozilardi-yu
                # ishlatilmasdi, shu sabab endi yopilgan kamera qayta ochilganda ham
                # sovuq boshlanardi.)
                cams.sort(key=lambda c: (-_last_view.get(str(c.id), 0.0),
                                         (c.name or "").lower()))

                keep: set[str] = set()
                # 1) Ayni ko'rilayotganlar — har doim issiq (byudjetdan tashqari)
                for c in cams:
                    if readers.get(c.stream_path, 0) > 0:
                        keep.add(str(c.id))
                # 2) Byudjet bo'yicha navbatdagi kameralarni (nom tartibida) qo'shamiz
                quota = _warm_budget
                for c in cams:
                    cid = str(c.id)
                    if cid in keep:
                        continue
                    if quota <= 0:
                        break
                    keep.add(cid)
                    quota -= 1

                warmed = 0
                for c in cams:
                    cid = str(c.id)
                    if cid in keep:
                        try:
                            await ensure_for_camera(db, c)  # warm/bump (idempotent)
                            await _warm_hls(c.stream_path)   # HLS muxerni ham issiq tut
                            warmed += 1
                        except EzvizConfigError:
                            pass
                        except Exception as exc:  # noqa: BLE001
                            log.warning("warm-keeper xato (%s): %s", c.stream_path, exc)
                    else:
                        # Byudjetdan tashqari va ko'rilmayotgan -> CPU bo'shatish uchun to'xtatamiz
                        pub = _publishers.get(cid)
                        if pub and pub.alive and readers.get(c.stream_path, 0) == 0:
                            await stop(cid)

            log.info("warm-keeper: CPU=%.0f%% byudjet=%d issiq=%d/%d",
                     cpu, _warm_budget, warmed, len(cams))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("warm-keeper sikl xatosi: %s", exc)
        await asyncio.sleep(settings.hik_warm_interval_seconds)


# Panelda "doim issiq" yoqilgan kameralar (Camera.keep_warm) — run_always_warm har
# siklda yangilaydi, idle-reaper shu to'plam + ALWAYS_WARM_CAMERA_IDS ni o'tkazib yuboradi.
_keep_warm_ids: set[str] = set()


def _always_ids() -> set[str]:
    return set(settings.always_warm_ids) | _keep_warm_ids


async def run_always_warm() -> None:
    """Tanlangan kameralarni 24/7 TAYYOR tutadi — "Ko'rish" bir zumda ochilsin.

    Ro'yxat = panelda "doim issiq" yoqilgan kameralar (`Camera.keep_warm`) +
    eski `settings.always_warm_ids` (env). Ular uchun har siklda:
      * publisher tirikligini kafolatlaymiz (o'lgan bo'lsa qayta ko'tariladi),
      * oqim HAQIQATAN oqayotganini tekshiramiz (quyida — "o'lik oqim"),
      * HLS muxerni ham isitamiz (`_warm_hls`) — aks holda birinchi tomoshabin
        muxerning sovuq startini (~4s) kutardi.
    Idle-reaper bu kameralarga tegmaydi (`_reap_once` da alohida tekshiriladi).

    "O'LIK OQIM" muammosi: jarayon tirik bo'lishi YETARLI EMAS. Bulut uzilganda
    ffmpeg RTSP publish'ni ochiq ushlab turadi — MediaMTX path `ready` qolaveradi,
    lekin yangi kadr kelmaydi. `ensure_for_camera` idempotent bo'lgani uchun bunday
    publisher'ga tegmasdi va kamera "issiq" ko'rinib, aslida o'lik turardi. Shuning
    uchun bytesReceived o'sishini kuzatamiz: ketma-ket `_STALL_LIMIT` siklda o'smasa
    (yoki path ready bo'lmasa) publisher MAJBURAN qayta ishga tushiriladi.

    Adaptiv warm-keeper'dan FARQI: bu ro'yxat qat'iy va CPU'ga qarab qisqarmaydi —
    shuning uchun ro'yxat qisqa bo'lishi kerak (har kamera ~0.15-0.38 yadro DOIMIY).
    RTSP kameralar bu yerda kerak emas: ular MediaMTX'ning o'zida on-demand va
    sovuq start ~1s — faqat EZVIZ/Hik bulut kameralari isitiladi.
    """
    from sqlalchemy import or_, select

    from vizora.core.db import SessionLocal

    ids = settings.always_warm_ids
    warned: set[str] = set()
    last_count = -1
    # stream_path -> (oxirgi bytesReceived, ketma-ket qotish soni)
    seen_bytes: dict[str, tuple[int, int]] = {}
    _STALL_LIMIT = 2  # 2 sikl (~40s) kadr kelmasa -> majburan qayta ishga tushiramiz
    last_summary = ""  # holat O'ZGARGANDAgina log yozamiz (har 20s spam bo'lmasin)
    while True:
        try:
            uuids = []
            for s in ids:
                try:
                    uuids.append(uuid.UUID(s))
                except ValueError:
                    if s not in warned:
                        log.warning("DOIM TAYYOR: '%s' yaroqli UUID emas — o'tkazildi", s)
                        warned.add(s)
            async with SessionLocal() as db:
                cams = (await db.execute(
                    select(Camera).where(
                        Camera.enabled.is_(True),
                        or_(Camera.keep_warm.is_(True), Camera.id.in_(uuids)),
                    )
                )).scalars().all()
                _keep_warm_ids.clear()
                _keep_warm_ids.update(str(c.id) for c in cams if c.keep_warm)
                if len(cams) != last_count:
                    log.info("DOIM TAYYOR kameralar: %d ta (panel=%d, env=%d)",
                             len(cams), len(_keep_warm_ids), len(ids))
                    last_count = len(cams)
                found = {str(c.id) for c in cams}
                for missing in (set(ids) - found):
                    if missing not in warned and missing in {str(u) for u in uuids}:
                        log.warning("DOIM TAYYOR: kamera %s topilmadi yoki o'chirilgan", missing)
                        warned.add(missing)
                stats = await _path_stats()
                for cam in cams:
                    if not cam.is_ezviz:
                        # RTSP/ISUP: MediaMTX manbani o'zi doimiy tortadi
                        # (sourceOnDemand=false) — [[camera_streams.configure]].
                        continue
                    sp = cam.stream_path
                    try:
                        # --- O'LIK OQIM tekshiruvi (publisher tirik bo'lsa ham) ---
                        pub = _publishers.get(str(cam.id))
                        if pub is not None and pub.alive:
                            ready, nbytes = stats.get(sp, (False, 0))
                            prev, stalls = seen_bytes.get(sp, (-1, 0))
                            if ready and nbytes > prev:
                                seen_bytes[sp] = (nbytes, 0)      # oqim tirik
                            else:
                                stalls += 1
                                seen_bytes[sp] = (nbytes, stalls)
                                if stalls >= _STALL_LIMIT:
                                    log.warning(
                                        "DOIM TAYYOR: %s QOTGAN (ready=%s, bytes=%d, "
                                        "%d sikl o'zgarmadi) — publisher qayta ishga tushirilmoqda",
                                        sp, ready, nbytes, stalls)
                                    await stop(str(cam.id))
                                    seen_bytes[sp] = (-1, 0)
                        await mediamtx.safe_ensure_path(sp)
                        await ensure_for_camera(db, cam)   # idempotent: tirik bo'lsa tegmaydi
                        await _warm_hls(sp)                # HLS muxer ham tayyor tursin
                    except EzvizConfigError as exc:
                        if cam.stream_path not in warned:
                            log.warning("DOIM TAYYOR sozlama xatosi (%s): %s",
                                        cam.stream_path, exc)
                            warned.add(cam.stream_path)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("DOIM TAYYOR xato (%s): %s", cam.stream_path, exc)

                # Holat xulosasi — faqat O'ZGARGANDA (diagnostika uchun).
                parts = []
                for c in cams:
                    if not c.is_ezviz:
                        continue
                    ready, _b = stats.get(c.stream_path, (False, 0))
                    parts.append(c.name + (": oqayapti" if ready else ": OQIM YO'Q"))
                summary = " | ".join(parts)
                if summary and summary != last_summary:
                    log.info("DOIM TAYYOR holati: %s", summary)
                    last_summary = summary
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("DOIM TAYYOR sikl xatosi: %s", exc)
        await asyncio.sleep(settings.always_warm_interval_seconds)


async def _warm_vtm_cache() -> bool:
    """Har EZVIZ hisobi uchun qurilma ro'yxati keshini OLDINDAN yangilaydi.

    Kesh TTL'i tugagach keyingi kamera ochilishi ~4-6s bulut metama'lumotiga
    tushardi (o'lchandi: issiq 0.24s vs sovuq 5.69s). Kameralar doim ochilib
    turmasa kesh KO'PINCHA sovuq bo'ladi — shuning uchun fonda yangilab turamiz.
    Narxi: hisob uchun 1 bulut so'rovi / (TTL/2). Bloklovchi qism thread'da.

    False qaytadi — kamida bitta hisobni isitib bo'lmadi (bulut xatosi); chaqiruvchi
    keyingi urinishni TTL/2 emas, TEZROQ rejalashtirishi kerak."""
    from sqlalchemy import select

    from vizora.core.db import SessionLocal
    from vizora.sources.ezviz import vtm_cache

    half = max(60.0, settings.ezviz_vtm_cache_seconds / 2)
    all_ok = True
    async with SessionLocal() as db:
        accs = list((await db.execute(select(EzvizAccount))).scalars().all())
    for acc in accs:
        key = str(acc.id)
        a = vtm_cache.age(key, "pagelist")
        if a is not None and a < half:
            continue                      # hali yangi — tegmaymiz
        provider = acc.provider or "ezviz"
        region = acc.region or (
            settings.hik_default_region if provider == "hik" else settings.ezviz_default_region
        )
        try:
            password = crypto.decrypt(acc.password_enc)
        except Exception:  # noqa: BLE001 — AES kalit mos kelmasa o'tkazib yuboramiz
            continue
        token_file = await tokens.ensure_token_file(
            key, acc.account, password, region, provider)
        # Keshni OLDINDAN o'chirmaymiz: `warm` o'zi `bypass_read()` bilan bulutga
        # boradi va FAQAT muvaffaqiyatda yozadi. Ilgari bu yerda `invalidate()`
        # bor edi — bulut 504 bersa kesh bo'sh qolib, o'sha hisobdagi kameralar
        # keyingi isitishgacha (TTL/2 = 30 daq) ~6s ortiq ochilardi.
        ok = await asyncio.to_thread(vtm_cache.warm, token_file, provider)
        if ok:
            log.info("VTM keshi isitildi (hisob=%s)", key)
        else:
            all_ok = False
    return all_ok


async def run_snapshot_warmer() -> None:
    """Fon: barcha EZVIZ kameralarning snapshot'ini SEKIN aylanib to'ldiradi.

    Nega: snapshot avval faqat TALAB bo'yicha (grid kartani ko'rsatganda) olinardi,
    ya'ni ko'rinmayotgan kamera hech qachon snapshot olmasди va grid ochilganda
    qora kvadrat ko'rinardi. Bu vazifa ularni oldindan to'ldiradi.

    Bulut `save_image` sekin (~2.5-4s) va bir hisob uchun bitta sessiya (ketma-ket),
    shuning uchun kameralar orasida pauza bilan BIRMA-BIR aylanamiz — bulutni
    hammalab yubormaymiz. To'liq sikl ~130 kamera × ~4s ≈ 8-9 daqiqa; snapshot
    muddati (interval) 10 daqiqa, ya'ni har sikl issiq tutish uchun yetarli.
    Oqim ishlab turgan kamera baribir jonli kadr beradi (arzon)."""
    from sqlalchemy import select

    from vizora.core.db import SessionLocal
    from vizora.models.camera import Camera
    from vizora.models.ezviz_account import EzvizAccount
    from vizora.core import crypto
    from vizora.services import snapshot as snap

    if not (settings.ezviz_snapshot_enabled and settings.ezviz_snapshot_warm_enabled):
        log.info("Snapshot-warmer o'chirilgan")
        return
    log.info("Snapshot-warmer ishga tushdi (gap=%.1fs)", settings.ezviz_snapshot_warm_gap_seconds)
    gap = max(0.5, settings.ezviz_snapshot_warm_gap_seconds)
    while True:
        try:
            async with SessionLocal() as db:
                rows = list((await db.execute(
                    select(Camera, EzvizAccount)
                    .join(EzvizAccount, EzvizAccount.id == Camera.ezviz_account_id)
                    .where(Camera.enabled.is_(True), Camera.source_type == "ezviz")
                    .order_by(Camera.name)
                )).all())
            for cam, acc in rows:
                # Kesh hali yangi bo'lsa refresh() o'zi o'tkazib yuboradi (arzon).
                try:
                    provider = acc.provider or "ezviz"
                    region = acc.region or (
                        settings.hik_default_region if provider == "hik"
                        else settings.ezviz_default_region)
                    from types import SimpleNamespace
                    cam_d = SimpleNamespace(id=cam.id, serial=cam.ezviz_serial,
                                            channel=cam.ezviz_channel,
                                            stream_path=cam.stream_path)
                    acc_d = SimpleNamespace(id=acc.id, account=acc.account,
                                            password=crypto.decrypt(acc.password_enc),
                                            region=region, provider=provider)
                    await snap.refresh(cam_d, acc_d)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — bitta kamera xatosi siklni to'xtatmasin
                    pass
                await asyncio.sleep(gap)
            if not rows:
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Snapshot-warmer sikl xatosi: %s", exc)
            await asyncio.sleep(30)


async def run_fault_checker() -> None:
    """Nosoz kameralarni VAQTI-VAQTI BILAN qayta tekshiradi.

    Nosozlik faqat kimdir ko'rmoqchi bo'lganda aniqlanadi — shunda kamera
    tuzalsa ham "nosoz" bo'lib qolib ketardi. Bu vazifa har
    `EZVIZ_FAULT_RECHECK_SECONDS` da nosozlar ro'yxatidan bir nechtasini
    (`_FAULT_CHECK_BATCH`) XOM rejimda ko'tarib ko'radi: path tayyor bo'lsa
    nosozlik olib tashlanadi. Tekshiruv arzon (enkodersiz) va sekin — bulutni
    hammalab yubormaslik uchun bir vaqtda faqat 2 kamera.
    """
    from vizora.core.db import SessionLocal

    interval = max(60.0, float(settings.ezviz_fault_recheck_seconds))
    log.info("Nosozlik tekshiruvchisi ishga tushdi (har %.0fs, %d kamera)",
             interval, _FAULT_CHECK_BATCH)
    while True:
        try:
            await asyncio.sleep(interval)
            if not _faults:
                continue
            now = time.monotonic()
            # Eng uzoq tekshirilmaganlardan boshlaymiz.
            due = sorted(_faults.items(), key=lambda kv: kv[1]["checked"])
            batch = [cid for cid, f in due
                     if now - f["checked"] >= interval][:_FAULT_CHECK_BATCH]
            if not batch:
                continue
            for cam_id in batch:
                f = _faults.get(cam_id)
                if f is None:
                    continue
                f["checked"] = time.monotonic()
                try:
                    async with SessionLocal() as db:
                        cam = await db.get(Camera, uuid.UUID(cam_id))
                        if cam is None or not cam.enabled or not cam.is_ezviz:
                            _faults.pop(cam_id, None)   # o'chirilgan — kuzatmaymiz
                            continue
                        await mediamtx.safe_ensure_path(cam.stream_path)
                        # XOM rejim: enkodersiz, CPU ~0 — shunchaki oqim keladimi?
                        await ensure_for_camera(db, cam, need_h264=False)
                        sp = cam.stream_path
                    if await wait_ready(sp, timeout=_FAULT_CHECK_TIMEOUT, warm_hls=False):
                        clear_fault(cam_id)
                    else:
                        f = _faults.get(cam_id)
                        if f:
                            f["tries"] += 1
                except EzvizConfigError as exc:
                    log.info("Nosozlik tekshiruvi o'tkazib yuborildi (%s): %s", cam_id, exc)
                except Exception as exc:  # noqa: BLE001 — tekshiruv siklini o'ldirmasin
                    log.warning("Nosozlik tekshiruvi xatosi (%s): %s", cam_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Nosozlik tekshiruvchisi xatosi: %s", exc)


async def run_supervisor() -> None:
    """Fon vazifasi: idle-reaper + token yangilash + VTM keshini issiq tutish."""
    log.info("EZVIZ supervisor ishga tushdi (idle=%ss)", settings.ezviz_idle_seconds)
    last_refresh = time.monotonic()
    last_vtm = 0.0
    vtm_gap = max(60.0, settings.ezviz_vtm_cache_seconds / 2)
    while True:
        try:
            await _reap_once()
            if time.monotonic() - last_refresh > settings.ezviz_token_refresh_seconds:
                await tokens.refresh_all()
                last_refresh = time.monotonic()
            # VTM keshini TTL/2 oraliqda yangilab turamiz (sovuq startni yo'qotadi).
            # Isitish yiqilsa (bulut 504/timeout) TTL/2 kutmaymiz: kesh eskirib
            # qolsa o'sha hisobdagi HAR bir kamera ~6s ortiq ochiladi, shuning
            # uchun tez qayta urinamiz (o'lchangan: issiq 0.14s vs sovuq 6.08s).
            if time.monotonic() - last_vtm > vtm_gap:
                last_vtm = time.monotonic()
                vtm_gap = (max(60.0, settings.ezviz_vtm_cache_seconds / 2)
                           if await _warm_vtm_cache()
                           else max(60.0, settings.ezviz_reaper_interval_seconds))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("EZVIZ supervisor xatosi: %s", exc)
        await asyncio.sleep(settings.ezviz_reaper_interval_seconds)
