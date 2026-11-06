"""EZVIZ bulut oqimini MediaMTX'ga RTSP publish qiluvchi mustaqil jarayon.

Backend (manager.py) buni har EZVIZ kamera uchun on-demand subprocess sifatida
ishga tushiradi. Jarayon bulutdan oqimni oladi, kerak bo'lsa dekodlaydi,
ffmpeg orqali H.264 ga o'girib MediaMTX'ga publish qiladi:

    bulut(VTM) -> [dekod] -> ffmpeg(H.264) -> rtsp://mediamtx:8554/cam/<id>

Bitta birlashgan yo'l (oqimli dekoder) — u oqim shifrlangan-yo'qligini VPS NAL
orqali AVTOMATIK aniqlaydi (toza VPS body 0x0c bilan boshlanadi):
  * shifrlangan (verification code bor) — har NAL body AES-ECB bilan ochiladi;
  * toza (kod yo'q, masalan ko'p NVR kanallari) — bo'sh kalit beriladi, dekoder
    o'tkazib yuboradi (kalit ishlatilmaydi).
Format VA codec avtomatik aniqlanadi (boshlang'ich paketlardan): RTP/HEVC,
RTP/H.264, MPEG-PS (HEVC yoki H.264), MPEG-TS. Aniqlangan codec ffmpeg kirish
formatiga (-f hevc/h264/mpegts) beriladi — aks holda xom Annex-B noto'g'ri
demux qilinadi. open_cloud_stream -> oqimli depaketlash -> Annex-B -> ffmpeg ->
H.264 (ABR: HD+SD) -> RTSP. Buferlash yo'q (ishlab turgan decrypt_proxy.py
mantig'i) — aks holda kadr kesiladi va ffmpeg dekodlay olmaydi.

Ishga tushirish (odatda manager.py chaqiradi):
    python -m vizora.sources.ezviz.publish \
        --serial GK8973616 --channel 1 --token-file /tmp/.../<ent>.json \
        --publish-url rtsp://mediamtx:8554/cam/<id> [--verify-code SCXYKW]
"""
from __future__ import annotations

import argparse
import os
import logging
import subprocess
import threading
import time
from collections import deque

from vizora.core.config import settings
from vizora.media import ffmpeg_hw
from vizora.sources.ezviz import cloud, vtm_cache
from vizora.sources.ezviz.decrypt import (
    _H264RtpDecryptor,
    _HevcRtpDecryptor,
    _PsStreamDecryptor,
    detect_rtp_codec,
)

log = logging.getLogger("vizora.ezviz.publish")

# Kirish (pipe) argumentlari — xom Annex-B/PS/TS uchun umumiy.
# probesize/analyzeduration KICHIK: xom H.264/HEVC'da ffmpeg'ga faqat SPS/PPS(/VPS)
# topish kerak — bu birinchi bir-ikki KB da bo'ladi. Avval 500ms/500KB kutilardi,
# bu SOVUQ STARTGA to'g'ridan-to'g'ri ~0.5s qo'shardi.
_FF_INPUT = [
    # DIQQAT: `nobuffer` QO'YMAYMIZ! U demuxer'ga probe paytidagi paketlarni
    # BUFERLAMASLIKNI aytadi va xom HEVC'da o'sha paketlar (VPS/SPS/PPS + BIRINCHI
    # IDR) shunchaki TASHLANADI -> dekoder P-freymlardan boshlaydi -> tomoshabin
    # kameraning KEYINGI IDR'igacha KUL RANG/buzilgan tasvir ko'radi. Aynan shu
    # "boshida buzilib keladi" shikoyatining sababi edi. Bir xil kirish faylida
    # o'lchandi: nobuffer bilan Y(low..high)=125..127 (tekis kul rang),
    # nobuffer'siz 54..190 (haqiqiy tasvir). Kechikish uchun `analyzeduration 0`
    # + kichik `probesize` yetarli — nobuffer'ning qo'shimcha foydasi yo'q.
    "-fflags", "discardcorrupt",
    # Xom HEVC/H.264 (pipe) timestamp'siz keladi. Kadrni KELISH (wall-clock) vaqti
    # bilan belgilaymiz -> oqim real vaqtga mos, HLS segmentlari muntazam yetadi.
    # MUHIM: bu faqat quvur BLOKLANMAGANDA to'g'ri ishlaydi — shuning uchun yozish
    # alohida thread'da ([[_FfWriter]]), aks holda kadrlar to'p-to'p "kech" belgilanib
    # oqim uzuq-uzuq ko'rinardi.
    "-use_wallclock_as_timestamps", "1",
    "-flags", "low_delay",
    # Kirish demuxer thread'i to'lib qolmasin (ABR'da ikki enkoder sekinlashsa).
    "-thread_queue_size", "1024",
]


def _is_missing_resource(exc: BaseException | None) -> bool:
    """Xato "qurilma bulut ro'yxatida yo'q" degani mi?

    `pyezvizapi.cloud_stream` shu holatda `Could not find VTM resource for serial …`
    ni ko'taradi. Bu keshni O'CHIRISH uchun asos EMAS: pagelist muvaffaqiyatli
    olingan, qurilma esa unda yo'q — ya'ni kamera oflayn YOKI kesh eskirgan
    (hisobga yangi kamera qo'shilgan). Ikkisini ajratish uchun
    [[_refresh_pagelist]] ro'yxatni bir marta bulutdan yangilaydi va shundan
    keyingina kamera oflayn deb hisoblanadi. Kutubxonada bu holat uchun alohida
    xato turi yo'q, shuning uchun matn bo'yicha aniqlanadi (kutubxona versiyasi
    o'zgarsa test buni ushlaydi)."""
    return exc is not None and "could not find vtm resource" in str(exc).lower()


# "Ro'yxatda yo'q" xatosidan keyin pagelist'ni bulutdan qayta olishning ENG KICHIK
# oralig'i. Bundan yangi kesh allaqachon bulutdagi haqiqat — qayta so'rash bekor
# (oflayn kamerani ketma-ket bosish bulutni urib qo'ymasin).
_PAGELIST_REFRESH_FLOOR = 30.0


def _refresh_pagelist(client, token_file: str, serial: str) -> bool:
    """Hisobning VTM pagelist keshini BULUTDAN majburan yangilaydi.

    Nega kerak: kesh HISOB bo'yicha, TTL 1 soat, fon isitishi TTL/2 da yuradi.
    Hisobga YANGI kamera qo'shilsa (yoki uzoq oflayn qurilma bulutga qaytsa),
    keshdagi ro'yxatda u YO'Q va `open_cloud_stream` `Could not find VTM resource
    for serial ...` beradi. Bu xato oflayn qurilma bilan bir xil ko'rinadi,
    shuning uchun kamera "OFLAYN" deb belgilanib, publisher chiqib ketardi — va
    keyingi fon isitishigacha (30 daqiqagacha) panelda video umuman chiqmasdi,
    holbuki kamera onlayn va mobil ilovada ishlardi. Jonli serverda o'lchandi
    (bitta yangi kamera): keshda 18 resurs, bulutda 19 — farq aynan
    shu kamera; kesh yangilangach oqim darhol ochildi (H264, 5s da 2.3 MB).

    Shuning uchun oflayn deb hisoblashdan OLDIN ro'yxatni BIR MARTA bulutdan
    yangilaymiz: yangi ro'yxatda ham bo'lmasa — kamera haqiqatan oflayn.

    Keshni O'CHIRMAYMIZ — [[vtm_cache.bypass_read]] bilan faqat O'QISHNI chetlab
    o'tamiz, o'ram esa yangi natijani o'zi yozadi. Bulut xato bersa eski ro'yxat
    joyida qoladi (`invalidate()` qilinsa, bitta xato so'rov o'sha hisobdagi
    HAMMA kameraning keshini bo'shatib qo'yardi).
    """
    key = vtm_cache.account_key(token_file)
    age = vtm_cache.age(key, "pagelist")
    if age is not None and age < _PAGELIST_REFRESH_FLOOR:
        return False                       # kesh yangi — ro'yxat haqiqatan shunday
    import pyezvizapi.cloud_stream as _cs
    try:
        with vtm_cache.bypass_read():
            _cs.get_vtm_page_list(client)
    except Exception as exc:  # noqa: BLE001 — yangilash "best effort"
        log.warning("VTM ro'yxatini yangilab bo'lmadi (hisob=%s): %s", key, exc)
        return False
    log.info("VTM ro'yxati bulutdan YANGILANDI (hisob=%s, kesh yoshi=%.0fs) — "
             "serial=%s uchun qayta urinamiz", key, age or -1, serial)
    return True


# Chiqish kodlari — manager shu bo'yicha kamerani KATEGORIYALAYDI:
#   0 — normal tugash (to'xtatildi)
#   3 — qurilma OQIM BERMADI: bulutda bor, `stream.start()` o'tadi, video yo'q
#   4 — qurilma bulut ro'yxatida YO'Q (oflayn)
# Busiz "online, lekin ochilmaydi" kameralar yashirin qolardi: probe ularni
# bulut ro'yxati bo'yicha "online" deb belgilar, foydalanuvchi esa bo'sh plitka
# ko'rardi.
EXIT_NO_STREAM = 3
EXIT_MISSING = 4

# Bulut qurilmani TANIYDI va `stream.start()` ham o'tadi, lekin VIDEO KELMAYDI —
# pyezvizapi 20s dan keyin "Device offline or unreachable: timed out waiting for
# VTM stream data" beradi. Bu qurilma/tarmoq tomonidagi muammo: qayta urinish
# natijani o'zgartirmaydi (o'lchandi: 161 kameralik sweep'da 14 kamera shu holatda,
# har biri 3-4 marta urinib bir xil xato). Ilgari CHEKSIZ urinilardi — publisher
# tirik qolib, [[ws_stream]] hub'i "manba kutilmoqda" deb 90s aldab turardi va
# har urinish bulutga so'rov + CPU sarflardi. Ikki urinishdan keyin chiqamiz:
# hub publisher o'limini sezib DARHOL aniq xabar beradi, keyingi ko'rish
# urinishida manager publisher'ni qaytadan boshlaydi.
_UNREACHABLE_MARKERS = ("device offline or unreachable",
                        "timed out waiting for vtm stream data")


def _is_unreachable(exc: BaseException | None) -> bool:
    """Xato "qurilma bulutda bor, lekin oqim bermayapti" degani mi?"""
    if exc is None:
        return False
    s = str(exc).lower()
    return any(m in s for m in _UNREACHABLE_MARKERS)


def _ff_input(input_format: str) -> list[str]:
    """Kirish argumentlari + formatga mos probe hajmi.

    Xom Annex-B (hevc/h264) — SPS/PPS(/VPS) birinchi KB'larda, demak probe MINIMAL
    (sovuq startdan ~0.5s yutamiz). MPEG-TS esa konteyner: PAT/PMT topish uchun
    haqiqatan ko'proq kerak, shuning uchun unga kattaroq probe qoldiramiz."""
    if input_format == "mpegts":
        probe = ["-analyzeduration", "500000", "-probesize", "500000"]
    else:
        probe = ["-analyzeduration", "0", "-probesize", "64k"]
    return [*_FF_INPUT, *probe, "-f", input_format, "-i", "pipe:0"]


def _enc(bitrate_kbps: int, *, sd: bool = False) -> list[str]:
    """Bitta rendition uchun enkoder + kalit-kadr argumentlari (GPU yoki CPU).

    SD (360p) standart holatda CPU'da kodlanadi — GeForce NVENC sessiya limiti
    (~8) faqat HD'ga sarflansin, shunda GPU'da ikki baravar ko'p kamera sig'adi."""
    allow_gpu = not (sd and settings.nvenc_sd_on_cpu)
    return [
        *ffmpeg_hw.encoder_args(bitrate_kbps, allow_gpu=allow_gpu),
        *ffmpeg_hw.keyframe_args(settings.ezviz_keyframe_seconds),
    ]


def _scale_chain() -> str:
    """filter_complex: bitta dekoddan HD + SD (ixtiyoriy CFR bilan).

    `ezviz_output_fps == 0` -> `fps` filtri QO'YILMAYDI: manbaning o'z kadr tezligi
    saqlanadi (mobil ilova qanday ko'rsatsa shunday). Avval majburan 15fps edi —
    manba 20-25fps bo'lsa har 4-5 kadrdan 1-2 tasi TASHLANARDI va harakat uzuq-uzuq
    ko'rinardi. >0 bo'lsa eski xatti-harakat (CFR) qaytadi."""
    hd_h = settings.ezviz_hd_max_height
    sd_h = settings.ezviz_sd_height
    fps = settings.ezviz_output_fps
    pre = f"fps={fps}," if fps and fps > 0 else ""
    return (
        f"[0:v]{pre}split=2[a][b];"
        f"[a]scale=-2:'min({hd_h},ih)'[hd];"
        f"[b]scale=-2:'min({sd_h},ih)'[sd]"
    )


# --- Kalit-kadr aniqlash (bufer to'lganda TOZA joyda tashlash uchun) ---

def _starts_keyframe(buf: bytes, codec: str) -> bool:
    """Annex-B bo'lagi IRAP/param-set NAL bilan boshlanadimi?

    Bufer to'lib ketganda oqimni O'RTASIDAN kesib tashlash buzilish beradi
    (dekoder ref kadrni yo'qotadi). Shuning uchun faqat kalit kadr / param-set
    (VPS/SPS/PPS) chegarasida tashlaymiz — natija toza "jonlilikka sakrash"."""
    if codec == "raw":
        return True   # rawvideo — har bo'lak butun kadr, har joy toza chegara
    i = buf.find(b"\x00\x00\x01")
    if i < 0 or len(buf) <= i + 4:
        return False
    h = buf[i + 3]
    if codec == "h264":
        return (h & 0x1F) in (5, 7, 8)          # IDR, SPS, PPS
    # HEVC: 6-bit tur — 16..21 IRAP (BLA/IDR/CRA), 32..34 VPS/SPS/PPS
    t = (h >> 1) & 0x3F
    return 16 <= t <= 21 or 32 <= t <= 34


class _FfWriter:
    """ffmpeg stdin'ga ALOHIDA thread'da yozadi — bulut o'quvchisi bloklanmaydi.

    NEGA: avval bulut paketlari o'qilgan thread'ning O'ZIDA `ff.stdin.write()`
    chaqirilardi. ffmpeg bir zum sekinlashsa (CPU to'yingan, RTSP publish sekin)
    quvur to'ladi va write BLOKLANADI -> bulut TCP soketi o'qilmay qoladi ->
    bulut tomon paket tashlaydi -> **kadrlar buziladi, oqim qotadi**. Mobil ilova
    bunday qilmaydi (uning jitter buferi bor) — shuning uchun telefonda toza.

    Endi: o'quvchi faqat navbatga qo'yadi (bloklanmaydi), yozish esa mustaqil
    thread'da. Navbat `ezviz_write_buffer_bytes` dan oshsa — bu ffmpeg real
    vaqtdan orqada qolgani — orqadagi eski ma'lumot KALIT KADR chegarasida
    tashlanadi va oqim jonli chetga sakraydi (buzilish emas, qisqa sakrash)."""

    __slots__ = ("_q", "_size", "_max", "_cv", "_ff", "_stop", "_thread",
                 "_codec", "_dropping", "_dropped_bytes", "_drops", "_since_key")

    def __init__(self, max_bytes: int, codec: str) -> None:
        self._q: deque[bytes] = deque()
        self._size = 0
        self._max = max(256 * 1024, int(max_bytes))
        self._cv = threading.Condition()
        self._ff = None
        self._stop = False
        self._codec = codec
        self._dropping = False
        self._dropped_bytes = 0
        self._drops = 0
        self._since_key = 0        # tashlash boshlangandan beri tashlangan bayt
        self._thread = threading.Thread(target=self._run, name="ff-writer", daemon=True)
        self._thread.start()

    def set_ff(self, proc) -> None:
        """Yangi ffmpeg jarayoni (qayta yaratilganda) — navbatni tozalaymiz."""
        with self._cv:
            self._ff = proc
            self._q.clear()
            self._size = 0
            self._dropping = False
            self._cv.notify_all()

    def write(self, data: bytes) -> None:
        """Bloklanmaydigan qo'yish. Bufer to'lgan bo'lsa kalit kadrni kutib tozalaydi."""
        if not data:
            return
        with self._cv:
            if self._size + len(data) > self._max:
                if not self._dropping:
                    self._dropping = True
                    self._since_key = 0
            if self._dropping:
                # XAVFSIZLIK CHEGARASI: kalit kadr topilmasa (masalan MPEG-TS yoki
                # kodek noto'g'ri aniqlangan) cheksiz tashlab qolmaymiz — bir bufer
                # hajmicha tashlangach baribir davom etamiz.
                forced = self._since_key > self._max
                if forced or _starts_keyframe(data, self._codec):
                    # Toza chegara: eski backlog'ni tashlab shu kalit kadrdan davom.
                    self._dropped_bytes += self._size
                    self._drops += 1
                    self._q.clear()
                    self._size = 0
                    self._dropping = False
                    log.warning("ffmpeg orqada qoldi — bufer tozalandi (%d-marta, "
                                "%d KB tashlandi%s). GPU/NVENC yoqilganmi?",
                                self._drops, self._dropped_bytes // 1024,
                                ", kalit kadrsiz" if forced else "")
                else:
                    self._dropped_bytes += len(data)
                    self._since_key += len(data)
                    return  # kalit kadr kelmaguncha tashlaymiz
            self._q.append(data)
            self._size += len(data)
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop and (not self._q or self._ff is None):
                    self._cv.wait(0.5)
                    if self._stop:
                        return
                if self._stop:
                    return
                chunk = self._q.popleft()
                self._size -= len(chunk)
                ff = self._ff
            try:
                ff.stdin.write(chunk)
            except (BrokenPipeError, OSError, ValueError, AttributeError):
                # ffmpeg o'ldi — asosiy sikl uni qayta yaratib set_ff() chaqiradi.
                with self._cv:
                    self._q.clear()
                    self._size = 0
                time.sleep(0.05)

    def alive(self) -> bool:
        return self._thread.is_alive()

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()


def _die_with_parent() -> None:
    """Bola (ffmpeg) ota (publisher) o'lganda BIRGA o'lsin (Linux PR_SET_PDEATHSIG).

    Nega: ota NOTOZA o'lса (SIGKILL, OOM, crash) `finally` ishlamaydi va ffmpeg
    yetim qolib NVENC sessiyasини USHLAB turadi. GeForce/GPU'да sessiya soni
    cheklangan — oqib ketса yangi enkoder ochilmaydi ("Could not open encoder")
    va kameralar umuman ochilмаy qoladi. Bu bilan yadro ota o'lган zahoti
    bolага SIGKILL yuboradi. execve'дан keyin ham saqlanadi (fork'дан keyin,
    exec'дан oldin o'rnatiladi)."""
    try:
        import ctypes
        import signal as _sig
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, _sig.SIGKILL)  # PR_SET_PDEATHSIG
    except Exception:  # noqa: BLE001 — Linux bo'lmasa/xato bo'lsa jimgina o'tamiz
        pass


def _reap(proc) -> None:
    """Jarayonni to'g'ri yopadi: terminate -> wait -> kill (zombie qoldirmaslik).

    _run_decrypt har qayta ulanishda yangi ffmpeg ochadi; eskisini reap qilmasak
    har reconnect'da zombie qoladi."""
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 — TimeoutExpired yoki boshqa
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _raw_output(raw_url: str | None) -> list[str]:
    """XOM (transkodsiz) chiqish — kirish oqimini QAYTA KODLAMASDAN `<path>/raw` ga.

    Panel pleyeri (WS + WebCodecs) HEVC'ni brauzerda o'zi dekodlaydi, ya'ni unga
    H.264 ga o'girish KERAK EMAS. O'lchandi (simli Hik, sovuq ochilish 2.41s):
    ffmpeg/NVENC init -> MediaMTX "ready" **0.97s** va undan keyin birinchi kalit
    kadrni kutish **0.44s** — jami ~1.4s, ya'ni vaqtning 58% i faqat transkodga
    ketardi. `-c:v copy` da enkoder umuman ochilmaydi va publisher IRAP darvozasi
    tufayli birinchi yuborilgan NAL allaqachon VPS/SPS/PPS+IDR bo'ladi.

    Asosiy path (`cam/<id>`) H.264 bo'lib QOLADI — HLS, WebRTC va tashqi
    iste'molchilar (ApiClient) o'zgarishsiz ishlaydi. Bu shunchaki QO'SHIMCHA
    chiqish; qayta kodlash yo'q, CPU ~0.

    Ro'yxatda BIRINCHI turadi: ffmpeg chiqishlarni tartib bo'yicha ochadi, shuning
    uchun xom path enkoderlar tayyor bo'lishini kutmaydi."""
    if not raw_url:
        return []
    return ["-map", "0:v", "-an", "-c:v", "copy",
            "-f", "rtsp", "-rtsp_transport", "tcp", raw_url]


def _ffmpeg_raw_only_cmd(input_format: str, raw_url: str) -> list[str]:
    """FAQAT xom chiqish — enkoder umuman ochilmaydi (CPU ~0.02 yadro).

    Panel pleyeri (WS + WebCodecs) kodlangan kadrlarni brauzerda dekodlaydi, ya'ni
    H.264 ga o'girish FAQAT HLS/WebRTC va tashqi iste'molchilar uchun kerak. Hech
    kim ularni so'ramayotgan bo'lsa transkod bekorga yonadi: o'lchandi — 14 ta
    issiq kamerada app konteyneri **15.5 yadro** yegan (har biri ~1 yadro, chunki
    GPU sessiyalari 6 tada cheklangach qolganlari libx264 ga tushadi).

    Shu sababli publisher endi IKKI rejimda ishlaydi: `--raw-only` (panel) va
    to'liq (iste'molchi bor). Rejim [[manager.ensure_for_camera]] da tanlanadi va
    H.264 kerak bo'lib qolsa publisher qayta ishga tushiriladi."""
    return [
        "ffmpeg", "-loglevel", "error",
        *_ff_input(input_format),
        *_raw_output(raw_url),
    ]


def _ffmpeg_cmd(input_format: str, hd_url: str, sd_url: str,
                raw_url: str | None = None) -> list[str]:
    """Bitta dekoddan IKKI rendition (ABR): HD (capped) + SD (past tarmoq).

    Manba bir marta dekod qilinadi, so'ng split bilan ikki o'lchamga kodlanadi va
    ikki alohida RTSP path'ga publish qilinadi (HD: cam/<id>, SD: cam/<id>/sub).
    hls.js master.m3u8'dan o'tkazuvchanlikka qarab HD↔SD avtomatik almashadi.
    `scale=-2:'min(H,ih)'` — nisbatni saqlaydi, hech qachon kattalashtirmaydi
    (kichik kamera bo'lsa o'zicha qoladi), kenglik juft (H.264 talabi).
    Enkoder GPU (h264_nvenc) yoki CPU (libx264) — [[ffmpeg_hw]] avtomatik tanlaydi."""
    return [
        "ffmpeg", "-loglevel", "error",
        *_ff_input(input_format),
        # XOM chiqish (panel uchun, transkodsiz) — enkoderlardan OLDIN ochiladi
        *_raw_output(raw_url),
        "-filter_complex", _scale_chain(),
        # HD rendition -> cam/<id>
        "-map", "[hd]", *_enc(settings.ezviz_hd_bitrate_kbps),
        "-f", "rtsp", "-rtsp_transport", "tcp", hd_url,
        # SD rendition -> cam/<id>/sub
        "-map", "[sd]", *_enc(settings.ezviz_sd_bitrate_kbps, sd=True),
        "-f", "rtsp", "-rtsp_transport", "tcp", sd_url,
    ]


def _ffmpeg_copy_cmd(input_format: str, hd_url: str, sd_url: str,
                     raw_url: str | None = None) -> list[str]:
    """Manba ALLAQACHON H.264 bo'lsa — TRANSKODSIZ (`-c:v copy`) remux.

    Dekod/enkod yo'q -> CPU ~0 (faqat demux + RTSP mux). Substream odatda H.264
    bo'lgani uchun ko'p kamerani shu yo'l bilan bir zumda issiq tutsa bo'ladi.
    ABR (HD↔SD) kerak emas (substream o'zi past o'lcham) — bir xil oqimni HD va SD
    path'larga ikki marta copy qilamiz, shunda hls_proxy master.m3u8 o'zgarmaydi
    (ikkala variant ham ishlaydi, sifat bir xil). Copy arzon — ikki marta ham ~0 CPU."""
    return [
        "ffmpeg", "-loglevel", "error",
        *_ff_input(input_format),
        # XOM path -> cam/<id>/raw (bu yerda HD bilan bir xil oqim; pleyer DOIM
        # /raw ni so'rashi uchun u har doim mavjud bo'lishi kerak)
        *_raw_output(raw_url),
        # HD path -> cam/<id>
        "-an", "-c:v", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", hd_url,
        # SD path -> cam/<id>/sub (xuddi shu oqim — ABR master buzilmasin)
        "-an", "-c:v", "copy", "-f", "rtsp", "-rtsp_transport", "tcp", sd_url,
    ]


def _run_decrypt(serial: str, channel: int, token_file: str, verify_code: str,
                 publish_url: str, provider: str = "ezviz", *, substream: bool = False,
                 raw_only: bool = False) -> None:
    """Bulut oqimini OQIMLI dekodlab (kerak bo'lsa AES-ECB) ffmpeg'ga uzatadi.

    Format VA codec avtomatik aniqlanadi (boshlang'ich paketlardan):
      * RTP/HEVC, RTP/H.264 — batareyali kamera/IPC (EZVIZ ham, Hik ham)
      * MPEG-PS (HEVC yoki H.264) — Hik-Connect NVR'lari
      * MPEG-TS — to'g'ridan ffmpeg demux qiladi (shifrsiz)
    Aniqlangan codec ffmpeg'ning kirish formati (-f hevc/h264/mpegts) bo'ladi —
    aks holda ffmpeg xom Annex-B'ni noto'g'ri demux qiladi. Verification code
    to'g'ridan AES kalit; kod yo'q bo'lsa toza oqim shunchaki o'tib ketadi (dekoder
    param-set NAL orqali shifrni avto-aniqlaydi).
    """
    from itertools import chain

    from pyezvizapi.cloud_stream import open_cloud_stream
    from pyezvizapi.stream import rtp_payload

    # VAZIYATGA QARAB (per-kamera): substream yoqilgan bo'lsa avval substream (past
    # o'lcham -> toza, arzon). Ochilmasa/paket bermasa (masalan batareyalida yo'q)
    # -> ASOSIY oqimga qaytamiz. sub_fails — ketma-ket substream xatolari soni.
    want_substream = substream
    sub_fails = 0

    # Inter-shifr qarori keshining kaliti: seriya + kanal = aynan bitta kamera.
    # (Bir NVR'ning har kanali alohida — ular turlicha bo'lishi mumkin.)
    _enc_key = f"{serial}-{channel}"
    missing_tries = 0      # ketma-ket "qurilma bulut ro'yxatida yo'q" xatolari
    unreach_tries = 0      # ketma-ket "bulutda bor, lekin oqim bermayapti" xatolari

    # --- Sovuq start diagnostikasi ---
    # Kamera ochilishi sekin bo'lsa vaqt QAYERGA ketayotgani shu loglardan ko'rinadi
    # (bulut handshake / birinchi paket / kodek aniqlash / ffmpeg). Taxmin qilib
    # optimallashtirmaslik uchun.
    _t0 = time.monotonic()

    def _lap(stage: str) -> None:
        log.info("[vaqt] %-22s %6.2fs (serial=%s)", stage, time.monotonic() - _t0, serial)

    client = cloud.make_client(token_file, provider, substream=want_substream)
    _lap("client tayyor")

    # ABR: HD asosiy path'ga, SD esa "<path>/sub" ga publish qilinadi (hls_proxy
    # master.m3u8 shu ikkisini birlashtiradi).
    hd_url = publish_url
    sd_url = f"{publish_url}/sub"
    # XOM (transkodsiz) chiqish — panel pleyeri shuni o'qiydi ([[_raw_output]])
    raw_url = f"{publish_url}/raw" if settings.ezviz_raw_path else None

    ff = None
    ff_started = 0.0   # oxirgi ffmpeg qachon boshlangan (tez o'lim = NVENC belgisi)
    writer: _FfWriter | None = None
    infmt = None  # 'hevc' | 'h264' | 'mpegts' — birinchi ulanishda aniqlanadi

    def _ensure_ff(fmt: str):
        """ffmpeg'ni UZOQ-YASHAR qilamiz — faqat o'lgan bo'lsa qayta yaratamiz.

        Bulut blip'larida (4G) ffmpeg va MediaMTX'ga RTSP publish UZILMAYDI:
        muxer reset bo'lmaydi -> tomoshabin to'liq uzilish ko'rmaydi (qisqa muzlash
        bo'lib, bulut qaytishi bilan davom etadi).

        Yozish ALOHIDA thread'da ([[_FfWriter]]) — bulut o'quvchisi hech qachon
        ffmpeg quvuriga bloklanmaydi (bloklanish = bulut paket tashlashi = buzilish)."""
        nonlocal ff, writer, ff_started
        if ff is None or ff.poll() is not None:
            if ff is not None:
                # NVENC sessiya chegarasi -> ffmpeg darhol o'ladi. Sababini
                # aniqlab, GPU'ni shu jarayon uchun o'chiramiz ([[ffmpeg_hw]]);
                # aks holda bulutga cheksiz qayta ulanib o'lib-tirilaverardik.
                ffmpeg_hw.note_encoder_death(time.monotonic() - ff_started)
                _reap(ff)
            # Panel uchungina kerak bo'lsa — FAQAT xom chiqish, enkodersiz.
            if raw_only and raw_url:
                cmd = _ffmpeg_raw_only_cmd(fmt, raw_url)
                mode = "XOM (enkodersiz)"
            # H.264 bo'lsa TRANSKODSIZ copy (CPU ~0); HEVC/boshqa bo'lsa transkod
            # (GPU bo'lsa h264_nvenc, aks holda libx264 — [[ffmpeg_hw]]).
            elif fmt == "h264":
                cmd = _ffmpeg_copy_cmd(fmt, hd_url, sd_url, raw_url)
                mode = "COPY (transkodsiz)"
            else:
                cmd = _ffmpeg_cmd(fmt, hd_url, sd_url, raw_url)
                mode = f"ABR HD+SD transkod ({ffmpeg_hw.encoder_name()})"
            ff = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=None,
                preexec_fn=_die_with_parent,   # ota o'lса ffmpeg ham o'lsin (NVENC oqmasin)
            )
            if writer is None:
                # MPEG-TS'da Annex-B start-code yo'q (188-baytli paketlar) —
                # "raw" rejimi har paket chegarasini toza deb qabul qiladi.
                wcodec = "raw" if fmt == "mpegts" else ("h264" if fmt == "h264" else "hevc")
                writer = _FfWriter(settings.ezviz_write_buffer_bytes, wcodec)
            writer.set_ff(ff)
            ff_started = time.monotonic()
            log.info("ffmpeg ishga tushdi — %s codec=%s (serial=%s)", mode, fmt, serial)
            _lap("ffmpeg ishga tushdi")
        return ff

    def _log_key_error():
        if verify_code:
            log.error("Verification code XATO (serial=%s) — kamera tasdiqlash kodi "
                      "noto'g'ri", serial)
        else:
            log.error("Oqim SHIFRLANGAN (serial=%s) — tasdiqlash kodi (verify code) "
                      "kerak", serial)

    backoff = 1
    try:
        while True:
            dec = None
            got_packets = False    # shu urinishda bulutdan paket keldimi?
            last_exc: BaseException | None = None
            try:
                with open_cloud_stream(client, serial, channel=channel, client_type=9,
                                       refresh_vtm=False, timeout=20.0) as stream:
                    _lap("bulut ulandi (VTM)")
                    stream.start()
                    _lap("stream.start()")
                    backoff = 1  # ulanish muvaffaqiyatli
                    pkts = stream.iter_packets()

                    # --- Boshlang'ich paketlardan format (RTP/PS/TS) + codec aniqlash ---
                    buffered: list[bytes] = []
                    is_rtp = None
                    rtp_codec = None
                    for pkt in pkts:
                        b = bytes(pkt.body)
                        if not buffered:
                            _lap("birinchi paket")
                            got_packets = True   # metama'lumot to'g'ri -> kesh to'g'ri
                        buffered.append(b)
                        if is_rtp is None:
                            is_rtp = len(b) >= 1 and (b[0] >> 6) == 2  # RTP v2
                        if not is_rtp:
                            break  # MPEG-PS yoki MPEG-TS — bittasi yetarli
                        if (b[1] & 0x7F) == 96:  # RTP video payload type
                            pl = rtp_payload(b)
                            if len(pl) >= 1:
                                rtp_codec = detect_rtp_codec(pl[0])
                                if rtp_codec:
                                    break
                        # codec marker kelguncha skanerlaymiz (uzun GOP / audio-old
                        # paketlar uchun yetarli oraliq)
                        if len(buffered) >= 400:
                            break
                    if not buffered:
                        raise OSError("oqimdan paket kelmadi")
                    sub_fails = 0  # paket keldi -> substream ishlayapti, hisoblagich nol

                    # --- Dekoder + ffmpeg kirish formatini tanlash ---
                    head = bytearray()
                    # rest — har doim bytes qaytaradi (buffered allaqachon bytes,
                    # pkts esa paket-obyektlar; aralashtirmaymiz).
                    if not is_rtp and buffered[0][:1] == b"\x47":
                        dec = None                       # MPEG-TS — ffmpeg o'zi demux
                        fmt = "mpegts"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))
                    elif not is_rtp:
                        dec = _PsStreamDecryptor(verify_code, cache_key=_enc_key)  # MPEG-PS (H.264/HEVC avto)
                        # codec/shifr aniqlanmaguncha paket feed qilamiz. PS dekoder
                        # bir nechta katta NAL yig'ib qaror qiladi (codec + enc_off);
                        # yuqori o'lchamli oqimda bitta kadr ko'p paketga bo'linadi,
                        # shuning uchun limit katta (yetmasa "hevc" default'ga tushib,
                        # noto'g'ri format -> chiqish bo'lmasligi mumkin edi).
                        for b in buffered:
                            head += dec.feed(b)
                        tries = 0
                        while dec.codec is None and not dec.key_error and tries < 1500:
                            nxt = next(pkts, None)
                            if nxt is None:
                                break
                            head += dec.feed(bytes(nxt.body))
                            tries += 1
                        fmt = dec.codec or "hevc"
                        rest = (bytes(p.body) for p in pkts)  # buffered allaqachon feed qilindi
                    elif rtp_codec == "h264":
                        dec = _H264RtpDecryptor(verify_code, cache_key=_enc_key)   # RTP/H.264
                        fmt = "h264"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))
                    else:
                        dec = _HevcRtpDecryptor(verify_code, cache_key=_enc_key)   # RTP/HEVC
                        fmt = "hevc"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))

                    if infmt is None:
                        infmt = fmt
                    _ensure_ff(infmt)  # bulut reconnect'da ffmpeg QAYTA YARATILMAYDI
                    log.info("EZVIZ oqim format=%s codec=%s (serial=%s)",
                             "RTP" if is_rtp else ("TS" if fmt == "mpegts" else "PS"),
                             infmt, serial)

                    if dec is not None and dec.key_error:
                        _log_key_error()
                        break
                    first_out = False   # ffmpeg'ga BIRINCHI bayt qachon ketdi
                    if head:
                        writer.write(bytes(head))
                        first_out = True
                        _lap("ffmpeg'ga birinchi kadr")

                    # ffmpeg tirikligini davriy tekshiramiz: yozish endi alohida
                    # thread'da bo'lgani uchun bu yerda BrokenPipe chiqmaydi.
                    n = 0
                    for body in rest:
                        ab = dec.feed(body) if dec is not None else body
                        if dec is not None and dec.key_error:
                            _log_key_error()
                            break
                        if ab:
                            writer.write(ab)
                            if not first_out:
                                first_out = True
                                # Bu nuqtaga qadar: bulut + kodek aniqlash + shifr
                                # varianti + IRAP kutish. Sovuq startning eng katta
                                # noma'lum qismi shu edi.
                                _lap("ffmpeg'ga birinchi kadr")
                        n += 1
                        if n % 200 == 0 and ff.poll() is not None:
                            log.warning("ffmpeg o'ldi (serial=%s) — qayta tiklash", serial)
                            break
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                log.warning("Bulut uzildi (serial=%s): %s — ffmpeg ochiq, qayta ulanmoqda", serial, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("Oqim xatosi (serial=%s): %s", serial, exc)
                last_exc = exc
            # Keshni FAQAT ochilish muvaffaqiyatsiz bo'lganda (bitta ham paket
            # kelmaganda) bekor qilamiz. Avval HAR uzilishdan keyin bekor
            # qilinardi — oqim yarim soat ishlab, keyin bulut blip'i bo'lsa ham.
            # Kesh HISOB bo'yicha, ya'ni bitta kameraning uzilishi o'sha hisobdagi
            # BARCHA kameralar keshini o'chirardi va keyingi har ochilish ~4-6s
            # metama'lumot so'roviga tushardi (o'lchandi: issiq kesh 0.14s,
            # bekor qilingandan keyin 5.76s). 200 kamerada bu bulutni ham uradi.
            # Paket kelgan bo'lsa metama'lumot TO'G'RI edi — kesh ham to'g'ri.
            # Token haqiqatan eskirgan bo'lsa KEYINGI urinish paketsiz tugaydi va
            # kesh o'sha yerda bekor qilinadi (bitta qo'shimcha urinish narxi).
            # "Ro'yxatda yo'q" xatosi metama'lumot ESKIRGANIDAN emas: pagelist
            # MUVAFFAQIYATLI olindi, qurilma esa unda yo'q (kamera OFLAYN — bulut
            # oflayn qurilmani ro'yxatdan chiqarib tashlaydi). Bunda keshni
            # o'chirish ZARARLI: kesh HISOB bo'yicha, ya'ni bitta oflayn kamerani
            # bosish o'sha hisobdagi HAMMA kameraning keshini yo'q qiladi va
            # ularning har biri yana ~4.5s metama'lumotga tushadi (real bazada
            # 151 kameradan 31 tasi oflayn edi — ya'ni bu doimiy sodir bo'lardi).
            # "Qurilma oqim bermadi" ham keshni BUZMASLIGI kerak: pagelist to'g'ri
            # olingan va `stream.start()` o'tgan, ya'ni metama'lumot YAROQLI —
            # qurilmaning o'zi video yubormadi. Kesh HISOB bo'yicha, shuning uchun
            # bitta shunday kamera butun hisobdagi kameralar keshini yo'q qilardi va
            # ularning har biri yana ~2.4s metama'lumotga tushardi (161 kameralik
            # sweep'da 14 kamera shu holatda edi -> batareyali kameralar hisobi doim sovuq turdi).
            if not got_packets and not _is_missing_resource(last_exc) and not _is_unreachable(last_exc):
                vtm_cache.invalidate(vtm_cache.account_key(token_file))
            # Oflayn qurilmani CHEKSIZ qayta urinish ham ma'nosiz: kamera qaytmaguncha
            # natija o'zgarmaydi. Chiqamiz — tomoshabin kelganda manager publisher'ni
            # qaytadan boshlaydi, va [[manager.wait_ready]] o'lgan publisher'ni sezib
            # 18s kutmasdan darhol qaytadi.
            if _is_missing_resource(last_exc):
                missing_tries += 1
                # Kesh eskirgan bo'lishi mumkin (hisobga YANGI kamera qo'shilgan yoki
                # qurilma bulutga qaytgan) — oflayn deb hisoblashdan OLDIN ro'yxatni
                # bir marta bulutdan yangilab, qayta urinib ko'ramiz.
                if missing_tries == 1:
                    _refresh_pagelist(client, token_file, serial)
                if missing_tries >= 2:
                    log.warning("Qurilma bulut ro'yxatida yo'q (serial=%s) — kamera "
                                "OFLAYN. Publisher to'xtatiladi.", serial)
                    raise SystemExit(EXIT_MISSING)
            else:
                missing_tries = 0
            # Qurilma bulutda bor, lekin oqim bermayapti — yuqoridagi izohga qarang.
            if _is_unreachable(last_exc) and not got_packets:
                unreach_tries += 1
                if unreach_tries >= 2:
                    log.warning("Qurilma oqim bermadi (serial=%s, %d urinish) — "
                                "publisher to'xtatiladi.", serial, unreach_tries)
                    raise SystemExit(EXIT_NO_STREAM)
            else:
                unreach_tries = 0
            # ffmpeg'ni bu yerda YOPMAYMIZ (uzluksiz publish). Faqat kod xato bo'lsa chiqamiz.
            if dec is not None and dec.key_error:
                return
            # VAZIYATGA QARAB fallback: substream ketma-ket ishlamasa -> asosiy oqim.
            if want_substream:
                sub_fails += 1
                if sub_fails >= 2:
                    log.warning("Substream ishlamadi (serial=%s) — ASOSIY oqimga qaytamiz", serial)
                    want_substream = False
                    client = cloud.make_client(token_file, provider, substream=False)
            time.sleep(min(backoff, 5))  # tezroq qayta ulanish (cap 5s)
            backoff = min(backoff * 2, 5)
    finally:
        if writer is not None:
            writer.close()
        if ff is not None:
            try:
                ff.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            _reap(ff)


def _ffmpeg_rawvideo_cmd(w: int, h: int, hd_url: str, sd_url: str) -> list[str]:
    """drop_corrupt yo'li uchun ffmpeg: kirish — PyAV bergan TOZA yuv420p rawvideo
    kadrlar (har biri arrival vaqti bilan); chiqish — _ffmpeg_cmd kabi ABR HD+SD."""
    return [
        "ffmpeg", "-loglevel", "error",
        # `nobuffer` yo'q — [[_FF_INPUT]] dagi izohga qarang (probe paketlarini
        # tashlab yuboradi). Rawvideo'da zarari kamroq, ammo foydasi ham yo'q.
        "-use_wallclock_as_timestamps", "1",
        "-thread_queue_size", "1024",
        "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}",
        "-i", "pipe:0",
        "-filter_complex", _scale_chain(),
        "-map", "[hd]", *_enc(settings.ezviz_hd_bitrate_kbps),
        "-f", "rtsp", "-rtsp_transport", "tcp", hd_url,
        "-map", "[sd]", *_enc(settings.ezviz_sd_bitrate_kbps, sd=True),
        "-f", "rtsp", "-rtsp_transport", "tcp", sd_url,
    ]


def _run_drop_corrupt(serial: str, channel: int, token_file: str, verify_code: str,
                      publish_url: str, provider: str = "ezviz", *, substream: bool = False) -> None:
    """Yo'qotuvchi (weak uplink) kameralar uchun: deshifrlangan oqimni PyAV bilan
    dekodlab FAQAT TOZA (xatosiz) kadrlarni ffmpeg'ga uzatadi. ffmpeg fps bilan
    oxirgi toza kadrni ushlab turadi -> past-fps, ammo BUZILISHSIZ. Faqat shu yo'l
    PyAV ishlatadi; oddiy kameralar [[_run_decrypt]] dan o'tadi (tegilmaydi)."""
    from itertools import chain

    import av
    from pyezvizapi.cloud_stream import open_cloud_stream

    _enc_key = f"{serial}-{channel}"
    from pyezvizapi.stream import rtp_payload

    # VAZIYATGA QARAB (per-kamera): substream yoqilgan bo'lsa avval substream,
    # ishlamasa asosiy oqim (xuddi _run_decrypt).
    want_substream = substream
    sub_fails = 0
    missing_tries = 0      # ketma-ket "qurilma bulut ro'yxatida yo'q" xatolari
    unreach_tries = 0      # ketma-ket "bulutda bor, lekin oqim bermayapti" xatolari
    client = cloud.make_client(token_file, provider, substream=want_substream)
    hd_url = publish_url
    sd_url = f"{publish_url}/sub"
    ff = None
    ff_started = 0.0   # oxirgi ffmpeg qachon boshlangan (tez o'lim = NVENC belgisi)
    writer: _FfWriter | None = None
    dims = None  # (w, h) — birinchi toza kadrdan

    def _ensure_ff(w, h):
        nonlocal ff, writer, ff_started
        if ff is None or ff.poll() is not None:
            if ff is not None:
                ffmpeg_hw.note_encoder_death(time.monotonic() - ff_started)  # NVENC? -> CPU
                _reap(ff)
            ff = subprocess.Popen(
                _ffmpeg_rawvideo_cmd(w, h, hd_url, sd_url),
                preexec_fn=_die_with_parent,   # ota o'lса ffmpeg ham o'lsin (NVENC oqmasin)
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=None,
            )
            if writer is None:
                # rawvideo: har bo'lak butun kadr — bufer to'lsa eng eski kadrlar
                # tashlanadi (bulut o'quvchisi/PyAV dekoderi bloklanmaydi).
                writer = _FfWriter(settings.ezviz_write_buffer_bytes, "raw")
            writer.set_ff(ff)
            ff_started = time.monotonic()
            log.info("ffmpeg ishga tushdi — drop_corrupt ABR HD+SD %dx%d %s (serial=%s)",
                     w, h, ffmpeg_hw.encoder_name(), serial)
        return ff

    def _log_key_error():
        if verify_code:
            log.error("Verification code XATO (serial=%s)", serial)
        else:
            log.error("Oqim SHIFRLANGAN (serial=%s) — tasdiqlash kodi kerak", serial)

    backoff = 1
    try:
        while True:
            dec = None
            pyav = None
            got_packets = False    # shu urinishda bulutdan paket keldimi?
            last_exc: BaseException | None = None
            try:
                with open_cloud_stream(client, serial, channel=channel, client_type=9,
                                       refresh_vtm=False, timeout=20.0) as stream:
                    stream.start()
                    backoff = 1
                    pkts = stream.iter_packets()
                    # --- format + codec aniqlash (RTP h264/hevc yoki PS) ---
                    buffered: list[bytes] = []
                    is_rtp = None
                    rtp_codec = None
                    for pkt in pkts:
                        b = bytes(pkt.body)
                        buffered.append(b)
                        got_packets = True
                        if is_rtp is None:
                            is_rtp = len(b) >= 1 and (b[0] >> 6) == 2
                        if not is_rtp:
                            break
                        if (b[1] & 0x7F) == 96:
                            pl = rtp_payload(b)
                            if len(pl) >= 1:
                                rtp_codec = detect_rtp_codec(pl[0])
                                if rtp_codec:
                                    break
                        if len(buffered) >= 400:
                            break
                    if not buffered:
                        raise OSError("oqimdan paket kelmadi")
                    sub_fails = 0  # paket keldi -> substream ishlayapti

                    head = bytearray()
                    if not is_rtp and buffered[0][:1] == b"\x47":
                        dec = None
                        fmt = "mpegts"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))
                    elif not is_rtp:
                        dec = _PsStreamDecryptor(verify_code, cache_key=_enc_key)
                        for b in buffered:
                            head += dec.feed(b)
                        tries = 0
                        while dec.codec is None and not dec.key_error and tries < 1500:
                            nxt = next(pkts, None)
                            if nxt is None:
                                break
                            head += dec.feed(bytes(nxt.body))
                            tries += 1
                        fmt = dec.codec or "hevc"
                        rest = (bytes(p.body) for p in pkts)
                    elif rtp_codec == "h264":
                        dec = _H264RtpDecryptor(verify_code, cache_key=_enc_key)
                        fmt = "h264"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))
                    else:
                        dec = _HevcRtpDecryptor(verify_code, cache_key=_enc_key)
                        fmt = "hevc"
                        rest = chain(buffered, (bytes(p.body) for p in pkts))

                    if dec is not None and dec.key_error:
                        _log_key_error()
                        break

                    # PyAV dekoder (TS bo'lsa konteyner, aks holda xom codec)
                    pyav = av.codec.CodecContext.create(
                        "hevc" if fmt == "mpegts" else fmt, "r")
                    log.info("drop_corrupt oqim format=%s codec=%s (serial=%s)",
                             "RTP" if is_rtp else ("TS" if fmt == "mpegts" else "PS"),
                             fmt, serial)

                    def _push(annexb):
                        nonlocal dims
                        for packet in pyav.parse(annexb):
                            try:
                                frames = pyav.decode(packet)
                            except Exception:  # noqa: BLE001 — buzuq paket -> tashlanadi
                                continue
                            for frame in frames:  # PyAV faqat TOZA kadrlarni beradi
                                if dims is None:
                                    dims = (frame.width, frame.height)
                                _ensure_ff(*dims)
                                if (frame.width, frame.height) != dims:
                                    continue  # o'lcham o'zgarsa o'tkazib yuboramiz
                                raw = frame.reformat(format="yuv420p").to_ndarray().tobytes()
                                writer.write(raw)
                                if ff.poll() is not None:
                                    raise OSError("ffmpeg o'ldi")

                    if head:
                        _push(bytes(head))
                    for body in rest:
                        ab = dec.feed(body) if dec is not None else body
                        if dec is not None and dec.key_error:
                            _log_key_error()
                            break
                        if ab:
                            _push(ab)
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                log.warning("Bulut/ffmpeg uzildi (serial=%s): %s — qayta ulanmoqda", serial, exc)
            except Exception as exc:  # noqa: BLE001
                log.warning("drop_corrupt oqim xatosi (serial=%s): %s", serial, exc)
                last_exc = exc
            # Keshni FAQAT paket kelmagan bo'lsa bekor qilamiz — sabab
            # [[_run_decrypt]] dagi izohda (hisob keshini behuda o'chirmaslik).
            # [[_run_decrypt]] dagi bilan bir xil sabab: oflayn qurilma hech
            # qachon paket bermaydi, ya'ni `got_packets` o'zi yetarli qo'riq emas.
            # "Qurilma oqim bermadi" ham keshni BUZMASLIGI kerak: pagelist to'g'ri
            # olingan va `stream.start()` o'tgan, ya'ni metama'lumot YAROQLI —
            # qurilmaning o'zi video yubormadi. Kesh HISOB bo'yicha, shuning uchun
            # bitta shunday kamera butun hisobdagi kameralar keshini yo'q qilardi va
            # ularning har biri yana ~2.4s metama'lumotga tushardi (161 kameralik
            # sweep'da 14 kamera shu holatda edi -> batareyali kameralar hisobi doim sovuq turdi).
            if not got_packets and not _is_missing_resource(last_exc) and not _is_unreachable(last_exc):
                vtm_cache.invalidate(vtm_cache.account_key(token_file))
            # drop_corrupt yo'lida ham oflayn/oqim bermaydigan qurilmada chiqamiz
            # (asosiy yo'ldagi bilan bir xil mantiq — [[_is_unreachable]]).
            if _is_missing_resource(last_exc):
                missing_tries += 1
                # Kesh eskirgan bo'lishi mumkin (hisobga YANGI kamera qo'shilgan yoki
                # qurilma bulutga qaytgan) — oflayn deb hisoblashdan OLDIN ro'yxatni
                # bir marta bulutdan yangilab, qayta urinib ko'ramiz.
                if missing_tries == 1:
                    _refresh_pagelist(client, token_file, serial)
                if missing_tries >= 2:
                    log.warning("Qurilma bulut ro'yxatida yo'q (serial=%s) — kamera "
                                "OFLAYN. Publisher to'xtatiladi.", serial)
                    raise SystemExit(EXIT_MISSING)
            else:
                missing_tries = 0
            if _is_unreachable(last_exc) and not got_packets:
                unreach_tries += 1
                if unreach_tries >= 2:
                    log.warning("Qurilma oqim bermadi (serial=%s, %d urinish) — "
                                "publisher to'xtatiladi.", serial, unreach_tries)
                    raise SystemExit(EXIT_NO_STREAM)
            else:
                unreach_tries = 0
            if dec is not None and dec.key_error:
                return
            if want_substream:
                sub_fails += 1
                if sub_fails >= 2:
                    log.warning("Substream ishlamadi (serial=%s) — ASOSIY oqimga qaytamiz", serial)
                    want_substream = False
                    client = cloud.make_client(token_file, provider, substream=False)
            time.sleep(min(backoff, 5))
            backoff = min(backoff * 2, 5)
    finally:
        if writer is not None:
            writer.close()
        if ff is not None:
            try:
                ff.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            _reap(ff)


def main() -> None:
    ap = argparse.ArgumentParser(description="EZVIZ bulut oqimini MediaMTX'ga publish qilish")
    ap.add_argument("--serial", required=True)
    ap.add_argument("--channel", type=int, default=1)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--publish-url", required=True)
    ap.add_argument("--verify-code", default=None, help="shifrlangan kamera dekod kodi")
    ap.add_argument("--provider", default="ezviz", choices=["ezviz", "hik"])
    ap.add_argument("--drop-corrupt", action="store_true",
                    help="buzuq kadrlarni tashlab faqat tozasini ko'rsatish (PyAV)")
    ap.add_argument("--raw-only", action="store_true",
                    help="faqat <path>/raw (enkodersiz) — panel uchun, CPU ~0")
    ap.add_argument("--substream", action="store_true",
                    help="bulut substream (past o'lcham) ni ishlatish (fallback bilan)")
    args = ap.parse_args()
    # Kod env orqali keladi (argv `ps` da ko'rinardi) va darhol o'chiriladi — ffmpeg
    # bolalari uni meros qilmasin. `--verify-code` qo'lda ishga tushirish uchun qoldi.
    args.verify_code = args.verify_code or os.environ.pop("VZ_VERIFY_CODE", None)
    # Past CPU ustuvorligi — ffmpeg bolalari ham meros oladi ([[config.publisher_nice]]).
    if settings.publisher_nice > 0:
        try:
            os.nice(settings.publisher_nice)
        except OSError:
            pass

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    if args.drop_corrupt:
        try:
            _run_drop_corrupt(args.serial, args.channel, args.token_file,
                              args.verify_code or "", args.publish_url, args.provider,
                              substream=args.substream)
        except KeyboardInterrupt:
            pass
        return
    # Doim oqimli dekoder yo'li — u oqim shifrlangan/shifrlanmaganligini VPS NAL
    # orqali AVTOMATIK aniqlaydi (toza VPS body 0x0c bilan boshlanadi). Verify code
    # yo'q bo'lsa bo'sh kalit beriladi: toza oqim (NVR kanallari odatda shunday)
    # shunchaki o'tib ketadi, kalit ishlatilmaydi; shifrlangan-u kodsiz bo'lsa
    # key_error bilan tushuntirib chiqadi. (pyezvizapi CLI passthrough'i bizning
    # token-file'ni yuklay olmaydi — "Missing authAddr" — shuning uchun ishlatilmaydi.)
    try:
        _run_decrypt(args.serial, args.channel, args.token_file,
                     args.verify_code or "", args.publish_url, args.provider,
                     substream=args.substream, raw_only=args.raw_only)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
