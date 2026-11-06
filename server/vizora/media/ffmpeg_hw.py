"""ffmpeg apparat (GPU) enkoderini aniqlash va enkoder argumentlarini qurish.

Nega kerak: EZVIZ/Hik bulut oqimlari HEVC keladi, brauzer uchun H.264 ga
transkod qilinadi. `libx264` (CPU) bitta 1080p kamerada ~0.3-0.5 yadro yeydi va
ABR (HD+SD) ikki marta kodlaydi. Bir necha kamera birdan ochilsa CPU to'yinadi →
enkoder real vaqtdan orqada qoladi → ffmpeg quvuri to'ladi → bulut o'quvchisi
bloklanadi → paket yo'qoladi → **oqim buziladi/qotadi**.

NVIDIA GPU bo'lsa `h264_nvenc` bu ishni ~0 CPU bilan va ANIQROQ sifatda
bajaradi. Aniqlash BIR MARTA (jarayon boshida) qilinadi va NATIJA KESHLANADI:
  1) `ffmpeg -encoders` da enkoder bormi;
  2) HAQIQIY sinov kodlash (1 kadr) — drayver/NVENC sessiyasi ishlayaptimi.
Sinov muvaffaqiyatsiz bo'lsa jimgina `libx264` ga qaytadi (CPU-only serverda ham
bir xil kod ishlaydi).

`FFMPEG_HWACCEL`: `auto` (standart — GPU bo'lsa ishlatadi) | `nvenc` (majburan)
| `none` (faqat CPU).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time

from vizora.core.config import settings

log = logging.getLogger("vizora.ffmpeg_hw")

# Aniqlash natijasi FAYLGA keshlanadi. Nega: har EZVIZ kamera uchun alohida
# publisher SUBPROCESS ishga tushadi va har biri sinov kodlashni qaytadan
# bajarardi — lokal o'lchovda bu sovuq startga ~0.7-2.8s qo'shdi. Kesh
# subprocess'lar orasida bo'lishiladi (vtm_cache bilan bir xil yondashuv).
_CACHE_TTL = 3600.0
# Salbiy natija QISQA keshlanadi: GPU vaqtincha band bo'lganda (NVENC sessiyalari
# to'lgan, boshqa konteyner GPU'da) sinov yiqiladi — bir soatlik "nvenc: false" keyin
# hamma kamerani CPU'da kodlashga majbur qilardi. Prod deploy'ida aynan shunday
# bo'ldi (2026-11-03): GPU bo'sh edi, kesh esa false edi.
_NEG_CACHE_TTL = 120.0


def _cache_path() -> str:
    return os.path.join(settings.ezviz_token_dir, "ffmpeg-hw.json")


def _cache_read() -> bool | None:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            d = json.load(f)
        ttl = _CACHE_TTL if d["nvenc"] else _NEG_CACHE_TTL
        if time.time() - float(d["ts"]) > ttl:
            return None
        return bool(d["nvenc"])
    except Exception:  # noqa: BLE001 — kesh yo'q/buzuq: qaytadan aniqlaymiz
        return None


def _cache_write(nvenc: bool) -> None:
    try:
        os.makedirs(settings.ezviz_token_dir, exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "nvenc": nvenc}, f)
        os.replace(tmp, _cache_path())   # atomik — yarim yozilgan kesh bo'lmaydi
    except Exception:  # noqa: BLE001 — kesh yozilmasa ham ishlaymiz
        pass

# NVENC past-kechikishli sozlama (ffmpeg >= 5.0: p1..p7 presetlar + `ull` tune).
# p1 = eng tez, ull = ultra-low-latency (B-kadr yo'q, lookahead yo'q).
#
# `-forced-idr 1` — MAJBURIY. Busiz `-force_key_frames` NVENC'da I-kadr beradi,
# lekin u **IDR EMAS**. MediaMTX HLS esa segmentni faqat IDR'dan boshlaydi:
# natijada RTSP path `ready` bo'lib, oqim oqib turadi-yu HLS muxer playlist
# YARATMAYDI — index.m3u8 abadiy osilib qoladi (kamera "ochilmaydi").
# Lokal o'lchov (4s, 25fps, har 1s kalit kadr so'ralgan):
#   forced-idr YO'Q -> 1 ta IDR (faqat birinchi kadr)
#   forced-idr BOR  -> 4 ta IDR (kutilgandek)
_NVENC_BASE = [
    "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull",
    "-profile:v", "high", "-rc", "cbr", "-zerolatency", "1",
    "-forced-idr", "1",
    "-delay", "0", "-bf", "0", "-rc-lookahead", "0", "-no-scenecut", "1",
]


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


# Jarayon ichidagi eslab qolish: ijobiy natija jarayon umri davomida, salbiy esa
# `_NEG_CACHE_TTL` dan keyin qayta tekshiriladi. Avval `lru_cache` edi — app
# jarayonida bir marta "false" bo'lsa, restartgacha hamma kamera CPU'da qolardi.
_memo: tuple[float, bool] | None = None


def _nvenc_works() -> bool:
    """NVENC HAQIQATAN ishlaydimi — ro'yxatda bor + sinov kodlash o'tdi.

    Natija fayl keshidan olinadi (publisher subprocess'lari sinovni qaytadan
    bajarmasin — bu sovuq startga sekundlar qo'shadi)."""
    global _memo
    now = time.monotonic()
    if _memo is not None and (_memo[1] or now - _memo[0] < _NEG_CACHE_TTL):
        return _memo[1]
    cached = _cache_read()
    if cached is None:
        cached = _probe_nvenc()
        _cache_write(cached)
    _memo = (now, cached)
    return cached


def _probe_nvenc() -> bool:
    ff = _ffmpeg_bin()
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception as exc:  # noqa: BLE001 — ffmpeg yo'q/ishlamadi
        log.warning("ffmpeg -encoders o'qilmadi: %s", exc)
        return False
    if "h264_nvenc" not in out:
        log.info("h264_nvenc ffmpeg'da yo'q — CPU (libx264) ishlatiladi")
        return False
    # Sinov: 1 kadr qora rasmni AYNAN shu argumentlar bilan kodlaymiz. Enkoder
    # ro'yxatda bo'lib, drayver/NVENC sessiyasi yo'q bo'lishi mumkin (Docker'da
    # nvidia runtime ulanmagan) — o'sha holatda ham xotirjam CPU'ga qaytamiz.
    probe = [ff, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
             *_NVENC_BASE, "-b:v", "500k", "-frames:v", "1", "-f", "null", "-"]
    try:
        r = subprocess.run(probe, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log.warning("NVENC sinovi bajarilmadi: %s — CPU ishlatiladi", exc)
        return False
    if r.returncode != 0:
        log.info("NVENC sinovi o'tmadi (%s) — CPU (libx264) ishlatiladi",
                 (r.stderr or "").strip().splitlines()[-1:] or "?")
        return False
    log.info("NVENC (h264_nvenc) TAYYOR — GPU transkod ishlatiladi")
    return True


def use_nvenc() -> bool:
    """Shu jarayonda GPU enkoder ishlatilsinmi?"""
    mode = (settings.ffmpeg_hwaccel or "auto").strip().lower()
    if mode == "none":
        return False
    if mode == "nvenc":
        # Majburan: sinovdan o'tmasa ham urinib ko'ramiz (foydalanuvchi aniq so'radi),
        # lekin ogohlantiramiz.
        if not _nvenc_works():
            log.warning("FFMPEG_HWACCEL=nvenc, lekin NVENC sinovi o'tmadi — "
                        "baribir urinib ko'ramiz")
        return True
    return _nvenc_works()


# --- Jarayon darajasida GPU'dan voz kechish (NVENC sessiya chegarasi) ---
# GeForce'da bir vaqtda ochiladigan NVENC sessiyalari cheklangan. Chegara oshsa
# ffmpeg "OpenEncodeSessionEx failed: incompatible client key (21)" / "No capable
# devices found" bilan DARHOL o'ladi va CPU'ga o'zi qaytmaydi. Publisher buni
# ko'rmasa bulutga har ~4s qayta ulanib cheksiz o'lib-tirilaveradi (2026-09-10 da
# 4 kamera 80-120s "Ulanmoqda" da qolib ketgan). Shu sabab: o'lim sababini
# ffmpeg'ning stderr'idan (bizning log faylimiz) o'qib, BU jarayon uchun GPU'ni
# o'chiramiz — keyingi ffmpeg libx264 bilan ko'tariladi.
NVENC_FAIL_MARKERS = (
    "OpenEncodeSessionEx failed",
    "No capable devices found",
    "Cannot load libnvidia-encode",
    "Cannot init CUDA",
)
_QUICK_DEATH_SECONDS = 4.0
_gpu_disabled_reason: str | None = None
_quick_deaths = 0


def gpu_disabled() -> str | None:
    """GPU shu jarayon uchun o'chirilgan bo'lsa — sababi, aks holda None."""
    return _gpu_disabled_reason


def disable_gpu(reason: str) -> None:
    global _gpu_disabled_reason
    if _gpu_disabled_reason is None:
        _gpu_disabled_reason = reason
        log.warning("GPU (NVENC) shu jarayon uchun O'CHIRILDI — %s; libx264 (CPU) bilan davom etamiz", reason)


def nvenc_failure_marker(text: str) -> str | None:
    for m in NVENC_FAIL_MARKERS:
        if m in text:
            return m
    return None


def _stderr_tail(n: int = 8000) -> str:
    """O'z stderr'imizning oxirgi baytlari (publisher stderr'i log faylga
    yo'naltirilgan; ffmpeg stderr'ni meros qilib oladi, demak uning xatolari ham
    shu yerda). Fayl bo'lmasa (terminal) bo'sh satr."""
    try:
        with open("/proc/self/fd/2", "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def note_encoder_death(alive_seconds: float, *, stderr_text: str | None = None) -> bool:
    """ffmpeg o'ldi — sabab NVENC bo'lsa GPU'ni o'chiradi. True = o'chirildi.

    Ikki belgi: (1) stderr'da NVENC xato satri; (2) enkoder GPU'da bo'lib ffmpeg
    ketma-ket 2 marta `_QUICK_DEATH_SECONDS` dan tez o'lsa (log o'qilmagan holat
    uchun zaxira). Uzoq yashagan ffmpeg o'limi (bulut/oqim sababli) hisobga olinmaydi."""
    global _quick_deaths
    if _gpu_disabled_reason is not None or not use_nvenc():
        return False
    text = _stderr_tail() if stderr_text is None else stderr_text
    marker = nvenc_failure_marker(text)
    if marker:
        disable_gpu(f"ffmpeg: {marker}")
        return True
    if alive_seconds < _QUICK_DEATH_SECONDS:
        _quick_deaths += 1
        if _quick_deaths >= 2:
            disable_gpu(f"ffmpeg {_quick_deaths} marta ketma-ket <{_QUICK_DEATH_SECONDS:.0f}s da o'ldi")
            return True
    else:
        _quick_deaths = 0
    return False


def encoder_name() -> str:
    return "h264_nvenc" if (use_nvenc() and _gpu_disabled_reason is None) else "libx264"


def encoder_args(bitrate_kbps: int, *, allow_gpu: bool = True) -> list[str]:
    """Bitta chiqish (rendition) uchun H.264 enkoder argumentlari.

    `allow_gpu=False` — bu rendition MAJBURAN CPU'da kodlanadi. Nega kerak:
    GeForce kartalarda bir vaqtda NVENC sessiyalari soni cheklangan (odatda 8).
    Har kamera ABR uchun 2 chiqish beradi (HD+SD) -> GPU'da faqat ~4 kamera
    sig'adi. SD (360p) esa CPU'da juda arzon (~0.03-0.05 yadro), shuning uchun
    standart holatda SD CPU'ga beriladi va GPU sessiyalari IKKI BARAVAR ko'proq
    kameraga yetadi (`NVENC_SD_ON_CPU=false` bilan o'chiriladi).

    Bitrate CBR-ga yaqin ushlanadi (`-b:v` = `-maxrate`): jonli oqimda bu eng
    barqarori — VBV doim qisilib turmaydi (avval faqat `-maxrate` berilib, CRF
    standart 23 qolgani uchun enkoder muttasil cheklanib "kvadratlashib" ketardi).
    Kalit kadr VAQT bo'yicha (`-force_key_frames`) chaqiruvchi tomonda beriladi —
    u past/notekis fps'da ham HLS segmentlarini qisqa ushlaydi.
    """
    br = f"{max(200, int(bitrate_kbps))}k"
    buf = f"{max(200, int(bitrate_kbps))}k"       # 1x — kechikish kichik bo'lsin
    if allow_gpu and use_nvenc() and _gpu_disabled_reason is None:
        return ["-an", *_NVENC_BASE, "-b:v", br, "-maxrate", br, "-bufsize", buf]
    return [
        "-an", "-c:v", "libx264",
        "-preset", settings.ffmpeg_x264_preset, "-tune", "zerolatency",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-b:v", br, "-maxrate", br, "-bufsize", f"{max(400, int(bitrate_kbps) * 2)}k",
        "-bf", "0", "-refs", "1", "-sc_threshold", "0",
    ]


def keyframe_args(keyframe_seconds: float) -> list[str]:
    """Kalit kadrni VAQT bo'yicha majburlash (kadr bo'yicha emas).

    Past/notekis fps kameralarda `-g <kadr>` GOP'ni ulkan qilib yuboradi va HLS
    segmentlari cho'zilib ketadi (sovuq start sekinlashadi). Vaqt bo'yicha
    majburlash NVENC'da ham, libx264'da ham ishlaydi."""
    s = max(0.5, float(keyframe_seconds))
    return ["-force_key_frames", f"expr:gte(t,n_forced*{s:g})"]


def warm() -> None:
    """Aniqlashni OLDINDAN bajaradi (app startup'da chaqiriladi).

    Shunda birinchi kamera ochilganda publisher subprocess'i sinov kodlashni
    kutmaydi — natija fayl keshidan darhol o'qiladi."""
    try:
        use_nvenc()
    except Exception as exc:  # noqa: BLE001 — startup'ni buzmasin
        log.warning("ffmpeg enkoderni aniqlash xatosi: %s", exc)
