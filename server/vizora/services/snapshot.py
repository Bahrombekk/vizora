"""Kamera SNAPSHOT (bitta kadr) — grid uchun arzon "ko'zni aldash".

Jonli transkod 100 kamera uchun qimmat (har biri ~0.35 yadro). Buning o'rniga
fonda har kameradan BITTA rasm (JPEG) olamiz: pyezvizapi `capture_picture` ->
download -> decrypt. Rasm keshlanadi; endpoint keshdagini DARROV beradi. Transkod
yo'q, jonli oqim pipeline'iga TEGMAYDI (xavfsiz). Foydalanuvchi kamerani ochsa,
jonli oqim alohida (manager.ensure_for_camera) ishga tushadi.

LAZY strategiya: faqat so'ralgan (grid'da ko'rinayotgan) kameralar yangilanadi —
100 kamerani befoyda olib o'tirmaymiz. Endpoint keshdagi (biroz eski bo'lsa ham)
rasmni qaytaradi va kesh eskirgan bo'lsa fonda yangi rasm olishni boshlaydi.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from types import SimpleNamespace

from vizora.core.config import settings

log = logging.getLogger("vizora.ezviz.snapshot")

# camera_id (str) -> (jpeg_bytes, monotonic_ts)
_cache: dict[str, tuple[bytes, float]] = {}
# ayni vaqtda rasm olinayotgan kameralar (takror so'rovlarni bostirish)
_inflight: set[str] = set()
# account_id (str) -> qurilgan pyezvizapi client (qayta foydalanish uchun keshlanadi)
_clients: dict[str, object] = {}
_client_locks: dict[str, asyncio.Lock] = {}
# Har HISOB uchun capture'ni KETMA-KET qilamiz: bulut sessiya bitta hisobdan
# parallel capture'ni ko'tarmaydi (timeout/device-exception beradi). Bittadan
# bo'lsa tez (0.6-2.3s) va ishonchli. Turli hisoblar parallel ishlayveradi.
_capture_locks: dict[str, asyncio.Lock] = {}


# Kesh DISKKA ham yoziladi: xotira keshi restartda yo'qoladi va deploy'dan keyin
# har kamera yana QORA ekran bo'lardi (yangi rasm kelguncha 0.6-2.3s bulut
# capture). Diskdagi oxirgi kadr esa darhol ko'rinadi. Papka `ezviz_token_dir`
# (compose'da nomli volume — deploy'lar orasida saqlanadi).
_JPEG_MAGIC = bytes([0xFF, 0xD8])


def _disk_path(cam_id: str) -> str:
    return os.path.join(settings.ezviz_token_dir, f"snap-{cam_id}.jpg")


def _disk_read(cam_id: str) -> tuple[bytes, float] | None:
    """Diskdagi rasmni o'qiydi (yoshi = fayl mtime'idan)."""
    try:
        st = os.stat(_disk_path(cam_id))
        with open(_disk_path(cam_id), "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:2] != _JPEG_MAGIC:
        return None
    age = max(0.0, time.time() - st.st_mtime)
    return data, time.monotonic() - age


def _disk_write(cam_id: str, data: bytes) -> None:
    try:
        os.makedirs(settings.ezviz_token_dir, exist_ok=True)
        tmp = _disk_path(cam_id) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, _disk_path(cam_id))   # atomik — yarim JPEG bo'lmaydi
    except OSError as exc:
        log.warning("snapshot diskka yozilmadi (%s): %s", cam_id, exc)


def get_cached(cam_id: str) -> bytes | None:
    e = _cache.get(cam_id)
    if e is None:
        e = _disk_read(cam_id)     # restartdan keyin: oxirgi kadr DARHOL
        if e is not None:
            _cache[cam_id] = e
    return e[0] if e else None


def clear() -> int:
    """Keshdagi barcha eski snapshot'larni tozalaydi (keyingi so'rovda 0'dan
    olinadi — qizigan kameralar uchun jonli-grab). Tozalangan soni qaytadi."""
    n = len(_cache)
    _cache.clear()
    try:
        for f in os.listdir(settings.ezviz_token_dir):
            if f.startswith("snap-") and f.endswith(".jpg"):
                os.unlink(os.path.join(settings.ezviz_token_dir, f))
                n = max(n, 1)
    except OSError:
        pass
    return n


def _age(cam_id: str) -> float | None:
    e = _cache.get(cam_id) or _disk_read(cam_id)
    return (time.monotonic() - e[1]) if e else None


def _client_lock(acc_id: str) -> asyncio.Lock:
    lk = _client_locks.get(acc_id)
    if lk is None:
        lk = _client_locks[acc_id] = asyncio.Lock()
    return lk


def _capture_lock(acc_id: str) -> asyncio.Lock:
    lk = _capture_locks.get(acc_id)
    if lk is None:
        lk = _capture_locks[acc_id] = asyncio.Lock()
    return lk


async def _get_client(acc: SimpleNamespace):
    """Hisob uchun pyezvizapi client'ni qaytaradi (kerak bo'lsa quradi/keshlaydi).

    cloud.make_client — sinovdan o'tgan quruvchi (hik uchun clientType
    va boshqa sozlamalar). Token fayli tokens orqali kafolatlanadi."""
    from vizora.sources.ezviz import cloud, tokens

    acc_id = str(acc.id)
    async with _client_lock(acc_id):
        client = _clients.get(acc_id)
        if client is None:
            tf = await tokens.ensure_token_file(
                acc_id, acc.account, acc.password, acc.region, acc.provider,
            )
            client = await asyncio.to_thread(cloud.make_client, tf, acc.provider)
            _clients[acc_id] = client
        return client


async def _grab_from_stream(stream_path: str) -> bytes | None:
    """Jonli MediaMTX oqimidan BITTA kadrni JPEG qilib oladi.

    Bu kadr — transkod qilingan jonli oqimning O'ZI: o'lcham, kadrlash va sifat
    AYNAN jonli video bilan bir xil (poster -> jonli o'tishda sakrash bo'lmaydi).
    Faqat oqim ayni vaqtda ishlab turgan bo'lsa ishlaydi (warm/ko'rilayotgan);
    bo'lmasa None (bulut capture'ga fallback)."""
    if not stream_path:
        return None
    from vizora.media import mediamtx

    url = mediamtx.rtsp_url(stream_path)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-protocol_whitelist", "rtsp,rtsps,rtp,srtp,tcp,udp,tls",  # SSRF/fayl o'qish yo'q
            "-i", url, "-frames:v", "1", "-q:v", "4", "-c:v", "mjpeg",
            "-f", "image2", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return None
        if proc.returncode == 0 and out[:2] == b"\xff\xd8":
            return out
    except (FileNotFoundError, OSError):
        pass
    return None


async def _grab_from_rtsp(rtsp_url: str) -> bytes | None:
    """To'g'ridan kamera RTSP URL'idan BITTA kadrni JPEG qilib oladi.

    RTSP (simli) kameralar uchun bulut `save_image`ning ekvivalenti: oqim ishlab
    turmagan (sovuq) bo'lsa MediaMTX'da path yo'q, shuning uchun kadrni kameraning
    O'ZIDAN olamiz. Bitta kadr — qisqa ulanish. Sovuq kameraga ulanib birinchi
    kalit-kadrni kutish 2-5s bo'lishi mumkin, shuning uchun timeout kengroq (6s).
    Kamera oflayn bo'lsa timeout -> None (endpoint 202 qaytaradi)."""
    if not rtsp_url:
        return None
    from vizora.media import mediamtx

    rtsp_url = mediamtx.with_creds(rtsp_url)   # ISUP kamera URL'i MediaMTX'ning o'zi
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-rtsp_transport", "tcp",
            "-protocol_whitelist", "rtsp,rtsps,rtp,srtp,tcp,udp,tls",  # SSRF/fayl o'qish yo'q
            "-i", rtsp_url, "-frames:v", "1",
            # Poster grid uchun to'liq 4MP sensor kadri (~900KB) shart emas —
            # kenglikni ≤1280 ga cheklab ~10x yengillashtiramiz (nisbat saqlanadi).
            "-vf", "scale='min(1280,iw)':-2", "-q:v", "4", "-c:v", "mjpeg",
            "-f", "image2", "pipe:1",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=6.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            return None
        if proc.returncode == 0 and out[:2] == b"\xff\xd8":
            return out
    except (FileNotFoundError, OSError):
        pass
    return None


async def start_refresh(db, cam, *, fresh: bool = False) -> None:
    """ORM kamerasi uchun fon yangilashni boshlaydi (so'rovni BLOKLAMAYDI).

    Endpoint'lar (admin va iste'molchi) uchun umumiy yopishtiruvchi: EZVIZ uchun
    hisobni o'qiydi/parolni deshifrlaydi, RTSP uchun kamera URL'ini deshifrlaydi,
    keyin [[refresh]] ni fon vazifasi qilib qo'yadi. Modul o'zi ORM'ga bog'liq
    bo'lmasin uchun importlar funksiya ichida. Xatolar YUTILADI — snapshot "chiroy"
    xizmati, u hech qachon oqimni yoki so'rovni yiqitmasligi kerak.
    """
    from vizora.core import crypto

    # RTSP (simli) kamera: hisob yo'q, kadrni kameraning o'zidan olamiz.
    if not getattr(cam, "is_ezviz", False):
        if not getattr(cam, "rtsp_url_enc", None):
            return
        # ISUP: asosiy oqim faqat ko'rilganda ochiq, substream esa doim — kadrni
        # shundan olamiz (aks holda snapshot uchun asosiy oqimni uyg'otish kerak bo'lardi).
        url_enc = cam.rtsp_url_enc
        if getattr(cam, "is_isup", False) and getattr(cam, "rtsp_sub_url_enc", None):
            url_enc = cam.rtsp_sub_url_enc
        try:
            cam_d = SimpleNamespace(id=cam.id, stream_path=cam.stream_path,
                                    rtsp_url=crypto.decrypt(url_enc))
            asyncio.create_task(refresh(cam_d, None, force=fresh))
        except Exception:  # noqa: BLE001 — deshifr xatosi snapshotni to'xtatmasin
            pass
        return

    if not cam.ezviz_account_id:
        return
    from vizora.models.ezviz_account import EzvizAccount

    acc = await db.get(EzvizAccount, cam.ezviz_account_id)
    if acc is None:
        return
    provider = acc.provider or "ezviz"
    region = acc.region or (
        settings.hik_default_region if provider == "hik" else settings.ezviz_default_region
    )
    try:
        cam_d = SimpleNamespace(id=cam.id, serial=cam.ezviz_serial,
                                channel=cam.ezviz_channel, stream_path=cam.stream_path)
        acc_d = SimpleNamespace(id=acc.id, account=acc.account,
                                password=crypto.decrypt(acc.password_enc),
                                region=region, provider=provider)
        asyncio.create_task(refresh(cam_d, acc_d, force=fresh))
    except Exception:  # noqa: BLE001 — parol deshifr xatosi snapshotni to'xtatmasin
        pass


async def refresh(cam: SimpleNamespace, acc: SimpleNamespace | None, *, force: bool = False) -> bool:
    """Kamera uchun yangi snapshot oladi va keshlaydi. True = muvaffaqiyat.

    Kesh hali yangi bo'lsa yoki ayni vaqtda olinayotgan bo'lsa — o'tkazib yuboradi.
    force=True — kesh yoshini e'tiborsiz qoldirib MAJBURAN yangilaydi (Player ochilganda
    real holatga yaqin rasm kerak; lekin har hisob uchun min 3s — hammerlamaslik).

    EZVIZ kamerada `acc` bulut hisobi bo'ladi; RTSP kamerada `acc is None` va cam'da
    `rtsp_url` bo'ladi (kadrni kameraning o'zidan olamiz)."""
    if not settings.ezviz_snapshot_enabled:
        return False
    cid = str(cam.id)
    if cid in _inflight:
        return False
    age = _age(cid)
    threshold = 3.0 if force else settings.ezviz_snapshot_interval_seconds
    if age is not None and age < threshold:
        return False

    _inflight.add(cid)
    try:
        # 1) AVVAL jonli oqimdan kadr — o'lcham/sifat jonli video bilan AYNAN bir xil
        #    (oqim ishlab turgan bo'lsa). Bu eng arzon va seamless.
        data = await _grab_from_stream(getattr(cam, "stream_path", None))

        # 2a) RTSP kamera sovuq bo'lsa — kadrni kameraning O'ZIDAN olamiz (bulut yo'q).
        if not data and getattr(cam, "rtsp_url", None):
            data = await _grab_from_rtsp(cam.rtsp_url)

        # 2b) EZVIZ kamera sovuq bo'lsa — bulut capture'ga fallback.
        if not data and acc is not None:
            client = await _get_client(acc)

            def _do() -> bytes:
                buf = io.BytesIO()
                client.save_image(cam.serial, buf, channel=cam.channel or 1, decrypt=True)
                return buf.getvalue()

            try:
                # Har hisob uchun KETMA-KET — parallel capture cloud sessiyani timeout
                # qildiradi. Lock ostida bittadan (tez va ishonchli).
                async with _capture_lock(str(acc.id)):
                    data = await asyncio.to_thread(_do)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                # MUHIM: bitta kameraning "kadr olib bo'lmadi" xatosi (qurilma OFLAYN,
                # kanal xato, "设备网络异常") HISOB SESSIYASINI buzmasligi kerak.
                # Avval HAR xatoда `_clients.pop` qilinardi -> bitta doim-xato kamera
                # (masalan GG7963860) butun hisob client'ini qayta-qayta o'chirib,
                # HAR snapshot'ni qayta login'ga tushirardi (thrashing) -> boshqa
                # kameralar ham snapshot ololmasди. Endi FAQAT haqiqiy sessiya/auth
                # xatosida qayta quramiz; qurilma-darajali xatoда client saqlanadi.
                device_level = ("Could not capture picture" in msg
                                or "capture" in msg.lower()
                                or "设备" in msg)
                if not device_level:
                    log.warning("snapshot sessiya xatosi (%s) — client qayta quriladi: %s",
                                cam.serial, msg[:120])
                    _clients.pop(str(acc.id), None)   # faqat AUTH/sessiya xatosida
                else:
                    log.debug("snapshot: kadr olib bo'lmadi (%s) — qurilma oflayn/kanal xato",
                              cam.serial)
                return False

        if data and data[:2] == b"\xff\xd8":  # JPEG magic
            _cache[cid] = (data, time.monotonic())
            _disk_write(cid, data)   # restart/deploy'dan keyin ham darhol ko'rinsin
            return True
        log.warning("snapshot JPEG emas (%s)", cam.serial)
        return False
    finally:
        _inflight.discard(cid)
