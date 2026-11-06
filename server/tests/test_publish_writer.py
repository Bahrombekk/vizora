"""EZVIZ publisher'ning yozuvchi thread'i va kalit-kadr aniqlashi.

Nega muhim: bulut o'quvchisi ffmpeg quvuriga BLOKLANMASLIGI kerak (bloklansa
bulut paket tashlaydi -> oqim buziladi/qotadi). Bufer to'lganda ma'lumot faqat
KALIT KADR chegarasida tashlanishi kerak — o'rtasidan kesish buzilish beradi.
"""
import time

import pytest

from vizora.core.config import settings
from vizora.media import ffmpeg_hw
from vizora.sources.ezviz import publish


def _annexb(nal_header: bytes, payload: bytes = b"\x00" * 64) -> bytes:
    return b"\x00\x00\x00\x01" + nal_header + payload


# --- Kalit kadr aniqlash ---

def test_h264_keyframe_nals():
    assert publish._starts_keyframe(_annexb(b"\x65"), "h264")   # IDR (tur 5)
    assert publish._starts_keyframe(_annexb(b"\x67"), "h264")   # SPS (7)
    assert publish._starts_keyframe(_annexb(b"\x68"), "h264")   # PPS (8)
    assert not publish._starts_keyframe(_annexb(b"\x41"), "h264")  # P-slice (1)


def test_hevc_keyframe_nals():
    assert publish._starts_keyframe(_annexb(b"\x26\x01"), "hevc")  # IDR_W_RADL (19)
    assert publish._starts_keyframe(_annexb(b"\x40\x01"), "hevc")  # VPS (32)
    assert publish._starts_keyframe(_annexb(b"\x42\x01"), "hevc")  # SPS (33)
    assert not publish._starts_keyframe(_annexb(b"\x02\x01"), "hevc")  # TRAIL_R (1)


def test_raw_always_keyframe():
    # rawvideo — har bo'lak butun kadr, har joyda tashlash xavfsiz
    assert publish._starts_keyframe(b"\x00" * 32, "raw")


def test_no_start_code_is_not_keyframe():
    assert not publish._starts_keyframe(b"\xde\xad\xbe\xef", "hevc")
    assert not publish._starts_keyframe(b"", "h264")


# --- Yozuvchi thread ---

class _FakeStdin:
    """Sekin ffmpeg stdin'i — har yozishda kechikadi (quvur to'lishini modellaydi)."""

    def __init__(self, delay: float = 0.02):
        self.delay = delay
        self.written = bytearray()

    def write(self, data):
        time.sleep(self.delay)
        self.written += data


class _FakeProc:
    def __init__(self, delay: float = 0.02):
        self.stdin = _FakeStdin(delay)

    def poll(self):
        return None


def test_writer_does_not_block_producer():
    """Ishlab chiqaruvchi (bulut o'quvchisi) yozish tezligiga BOG'LANMAYDI."""
    proc = _FakeProc(delay=0.05)          # juda sekin "ffmpeg"
    w = publish._FfWriter(1 << 20, "hevc")
    w.set_ff(proc)
    try:
        t0 = time.monotonic()
        for _ in range(50):
            w.write(_annexb(b"\x02\x01", b"\x00" * 1024))
        elapsed = time.monotonic() - t0
        # 50 × 50ms = 2.5s bloklangan bo'lardi; navbat orqali ~0s
        assert elapsed < 0.5, f"ishlab chiqaruvchi bloklandi: {elapsed:.2f}s"
    finally:
        w.close()


def test_writer_drops_only_at_keyframe():
    """Bufer to'lganda faqat KALIT KADR chegarasida tozalanadi."""
    proc = _FakeProc(delay=0.5)           # deyarli qotgan ffmpeg
    w = publish._FfWriter(256 * 1024, "hevc")   # eng kichik ruxsat etilgan bufer
    w.set_ff(proc)
    try:
        big = b"\x00" * 200_000
        # Buferni to'ldiramiz (P-kadrlar) -> "dropping" holatiga o'tadi
        for _ in range(6):
            w.write(_annexb(b"\x02\x01", big))
        assert w._dropping or w._drops >= 1
        # Kalit kadr keldi -> navbat tozalanadi, dropping tugaydi
        w.write(_annexb(b"\x26\x01", b"\x00" * 128))
        assert not w._dropping
        assert w._drops >= 1
    finally:
        w.close()


def test_writer_survives_dead_ffmpeg():
    """ffmpeg o'lsa yozuvchi thread yiqilmaydi (asosiy sikl uni qayta yaratadi)."""
    class _Dead:
        class stdin:
            @staticmethod
            def write(_):
                raise BrokenPipeError("o'ldi")

    w = publish._FfWriter(1 << 20, "hevc")
    w.set_ff(_Dead())
    try:
        for _ in range(5):
            w.write(_annexb(b"\x02\x01"))
        time.sleep(0.3)
        assert w.alive(), "yozuvchi thread BrokenPipe'dan keyin o'lib qolmasligi kerak"
    finally:
        w.close()


# --- Enkoder / filtr argumentlari ---

def test_scale_chain_passthrough_fps(monkeypatch):
    """EZVIZ_OUTPUT_FPS=0 -> `fps` filtri YO'Q (manba kadr tezligi saqlanadi)."""
    monkeypatch.setattr(settings, "ezviz_output_fps", 0)
    assert "fps=" not in publish._scale_chain()
    monkeypatch.setattr(settings, "ezviz_output_fps", 15)
    assert "fps=15," in publish._scale_chain()


def test_encoder_args_are_cbr_like():
    """Bitrate CBR-ga yaqin: `-b:v` = `-maxrate` (avval faqat maxrate edi ->
    CRF standart qolib enkoder muttasil VBV'ga urilardi = kvadratlar)."""
    args = ffmpeg_hw.encoder_args(2000, allow_gpu=False)
    assert args[args.index("-b:v") + 1] == "2000k"
    assert args[args.index("-maxrate") + 1] == "2000k"
    assert "libx264" in args


def test_sd_rendition_can_avoid_gpu(monkeypatch):
    """NVENC sessiya limitini tejash: SD CPU'da kodlanadi."""
    monkeypatch.setattr(settings, "nvenc_sd_on_cpu", True)
    monkeypatch.setattr(ffmpeg_hw, "use_nvenc", lambda: True)
    assert "libx264" in publish._enc(800, sd=True)
    assert "h264_nvenc" in publish._enc(3500)


def test_keyframe_args_time_based():
    assert ffmpeg_hw.keyframe_args(1.0) == ["-force_key_frames", "expr:gte(t,n_forced*1)"]
    assert ffmpeg_hw.keyframe_args(0.1)[1].endswith("*0.5)")  # pastki chegara


def test_writer_forced_clear_without_keyframe():
    """Kalit kadr topilmasa ham (MPEG-TS / kodek xato aniqlangan) cheksiz
    tashlab qolmaydi — bir bufer hajmicha tashlangach davom etadi."""
    proc = _FakeProc(delay=0.5)
    w = publish._FfWriter(256 * 1024, "hevc")
    w.set_ff(proc)
    try:
        chunk = b"\xde\xad" * 60_000        # start-code YO'Q -> hech qachon kalit emas
        for _ in range(12):
            w.write(chunk)
        assert not w._dropping, "xavfsizlik chegarasi ishlamadi — abadiy tashlash"
        assert w._drops >= 1
    finally:
        w.close()


def test_mpegts_writer_uses_raw_mode():
    """MPEG-TS'da Annex-B start-code yo'q — har paket chegarasi toza hisoblanadi."""
    w = publish._FfWriter(256 * 1024, "raw")
    try:
        assert publish._starts_keyframe(b"\x47" + b"\x00" * 187, "raw")
    finally:
        w.close()


def test_nvenc_forces_idr(monkeypatch):
    """NVENC'da `-forced-idr 1` BO'LISHI SHART.

    Busiz `-force_key_frames` I-kadr beradi, ammo IDR emas; MediaMTX HLS esa
    segmentni faqat IDR'dan boshlaydi -> RTSP `ready` bo'lsa ham playlist
    yaratilmaydi va kamera "ochilmaydi" (lokal sinovda aynan shu sodir bo'ldi)."""
    monkeypatch.setattr(ffmpeg_hw, "use_nvenc", lambda: True)
    args = ffmpeg_hw.encoder_args(3500)
    assert "h264_nvenc" in args
    assert args[args.index("-forced-idr") + 1] == "1"


def test_mediamtx_nvenc_cmd_forces_idr(monkeypatch):
    """RTSP (runOnDemand) transkod buyrug'ida ham xuddi shunday."""
    from vizora.media import mediamtx
    monkeypatch.setattr(settings, "mediamtx_transcode_encoder", "h264_nvenc")
    cmd = mediamtx._transcode_cmd("rtsp://cam/1")
    assert "-forced-idr 1" in cmd
    assert "-force_key_frames" in cmd


def test_input_flags_never_use_nobuffer():
    """`-fflags nobuffer` BO'LMASLIGI kerak.

    U demuxer'ga probe paketlarini buferlamaslikni aytadi va xom HEVC'da
    VPS/SPS/PPS + BIRINCHI IDR shunchaki tashlanadi -> dekoder P-freymdan
    boshlaydi -> tomoshabin kameraning keyingi IDR'igacha KUL RANG ko'radi.
    Bir xil kirish faylida o'lchandi: nobuffer bilan Y(low..high)=125..127
    (tekis kul rang), nobuffer'siz 54..190 (haqiqiy tasvir)."""
    for fmt in ("hevc", "h264", "mpegts"):
        args = publish._ff_input(fmt)
        joined = " ".join(args)
        assert "nobuffer" not in joined, f"{fmt}: nobuffer qaytib kelgan"
    raw = publish._ffmpeg_rawvideo_cmd(640, 360, "rtsp://a", "rtsp://b")
    assert "nobuffer" not in " ".join(raw)


def test_mediamtx_transcode_has_no_nobuffer():
    """RTSP (runOnDemand) transkodida ham xuddi shu xato bo'lmasin."""
    from vizora.media import mediamtx
    assert "nobuffer" not in mediamtx._transcode_cmd("rtsp://cam/1")


def test_vtm_cache_invalidated_only_on_failed_open():
    """Kesh FAQAT paket kelmaganda bekor qilinadi.

    Kesh HISOB bo'yicha: bitta kameraning uzilishi o'sha hisobdagi barcha
    kameralar keshini o'chirardi va keyingi har ochilish ~4-6s metama'lumot
    so'roviga tushardi (o'lchandi: issiq kesh 0.14s, bekor qilingandan keyin
    5.76s). Ikkala yo'lda ham (oddiy va drop_corrupt) shart qo'yilgan."""
    import inspect
    from vizora.sources.ezviz import publish as pub
    src = inspect.getsource(pub)
    # Har `vtm_cache.invalidate` chaqiruvi `if not got_packets:` ostida bo'lishi kerak
    lines = src.splitlines()
    calls = [i for i, l in enumerate(lines) if "vtm_cache.invalidate(" in l]
    assert calls, "invalidate chaqiruvi topilmadi"
    for i in calls:
        guard = lines[i - 1].strip()
        # Qo'riq KUCHAYTIRILDI: `got_packets` o'zi yetarli emas edi — OFLAYN kamera
        # ham hech qachon paket bermaydi, ya'ni uni bosish har safar keshni
        # o'chirardi. Endi "qurilma ro'yxatda yo'q" holati alohida chiqarib
        # tashlanadi ([[publish._is_missing_resource]]).
        assert guard.startswith("if not got_packets"), (
            f"{i + 1}-qatordagi invalidate shartsiz: {guard!r}")
        assert "_is_missing_resource" in guard, (
            f"{i + 1}-qatorda oflayn kamera qo'rig'i yo'q: {guard!r}")
