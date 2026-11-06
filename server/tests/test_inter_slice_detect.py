"""Inter (P/B) slice shifrini avto-aniqlash.

Nega kerak: ba'zi kameralar FAQAT IRAP + param-set NAL'larini shifrlaydi (inter
slice'lar toza), boshqalari esa HAMMASINI. Noto'g'ri taxmin har P-freymni buzadi.
Lokal o'lchov (BD8712447, 40s oqim): selektiv taxmin bilan 246 HEVC xatosi,
to'g'ri aniqlanganda 1. Teskari xato ham xuddi shunday yomon (toza P-freymni
deshifrlash uni buzadi), shuning uchun ikki yo'nalish ham tekshiriladi.
"""
import pytest

from vizora.sources.ezviz.decrypt import _HevcRtpDecryptor


def _dec():
    # Kalit ahamiyatsiz — qaror FAQAT baytlar tuzilishiga qarab qabul qilinadi.
    return _HevcRtpDecryptor("SCXYKW")


def test_structured_clear_bytes_stay_clear(monkeypatch):
    """Toza baytlar TAKRORLANUVCHI bo'lsa (haqiqiy slice header) — tegilmaydi."""
    d = _dec()
    # Toza: doim bir xil bayt (0xC1) — tuzilishli. Deshifr: tasodifiy.
    monkeypatch.setattr(d, "_aes16", lambda b: bytes([(hash(bytes(b[:4])) & 0xFF)]) + b"\0" * 15)
    for i in range(24):
        d._inter_clear.append(0xC1)
        d._inter_dec.append(i * 7 % 251)      # 24 xil qiymat -> tasodifiy
    d._decide_inter()
    assert d._inter_enc is False


def test_structured_decrypted_bytes_switch_to_decrypt():
    """Deshifrlangan baytlar tuzilishli bo'lsa — inter slice ham shifrlangan."""
    d = _dec()
    for i in range(16):
        d._inter_clear.append(i * 13 % 251)   # tasodifiy
        d._inter_dec.append(0xC1)             # bir xil -> tuzilishli
    d._decide_inter()
    assert d._inter_enc is True


def test_ambiguous_defaults_to_clear():
    """Ikkisi ham tasodifiy/teng bo'lsa — ESKI (xavfsiz) xatti-harakat: toza."""
    d = _dec()
    for i in range(16):
        d._inter_clear.append(i * 13 % 251)
        d._inter_dec.append(i * 17 % 251)
    d._decide_inter()
    assert d._inter_enc is False


def test_margin_required():
    """Ozgina farq YETARLI EMAS (chegara 0.25) — toza qoladi."""
    d = _dec()
    # toza: 16 xil (1.00), deshifr: 14 xil (0.875) -> farq 0.125 < 0.25
    for i in range(16):
        d._inter_clear.append(i)
        d._inter_dec.append(min(i, 13))
    d._decide_inter()
    assert d._inter_enc is False


def test_buffer_preserves_order():
    """Qaror kutilayotganda NAL'lar buferlanadi va TARTIB bilan chiqadi.

    Aks holda birinchi ~16 P-freym noto'g'ri ishlov bilan ffmpeg'ga borib
    buzilish berardi (o'lchovda 160 xato satri)."""
    d = _dec()
    out = bytearray()
    d._out(out, b"AAA")           # buferga (pending bo'sh ro'yxat)
    d._out(out, b"BBB")
    assert bytes(out) == b"", "qaror kutilayotganda chiqarilmasligi kerak"
    d._inter_enc = False
    d._flush(out)
    assert bytes(out) == b"AAABBB"


def test_flush_is_idempotent_after_decision():
    """Qarordan keyin _out darhol chiqaradi (bufer yo'q)."""
    d = _dec()
    out = bytearray()
    d._inter_enc = False
    d._pending = None
    d._out(out, b"XYZ")
    assert bytes(out) == b"XYZ"


# --- IRAP darvozasi (boshidagi buzilishga qarshi) ---

def test_irap_gate_drops_pre_irap_slices():
    """IRAP kelguncha inter slice CHIQMAYDI — dekoder ref kadrsiz kul rang berardi."""
    d = _dec()
    d._seen_irap = False
    assert d._irap_gate(1, 2) is False       # TRAIL_R — tashlanadi
    assert d._irap_gate(0, 2) is False       # TRAIL_N — tashlanadi


def test_irap_gate_lets_param_sets_through():
    """VPS/SPS/PPS IRAP'dan OLDIN ham o'tadi — dekoderga ular kerak."""
    d = _dec()
    for t in (32, 33, 34):
        assert d._irap_gate(t, 2) is True
    assert d._seen_irap is False              # param-set darvozani ochmaydi


def test_irap_opens_gate_permanently():
    d = _dec()
    assert d._irap_gate(19, 2) is True        # IDR_W_RADL
    assert d._seen_irap is True
    assert d._irap_gate(1, 2) is True         # endi hammasi o'tadi


def test_h264_irap_is_idr():
    d = _dec()
    assert d._irap_gate(1, 1) is False        # H.264 non-IDR slice
    assert d._irap_gate(5, 1) is True         # IDR


def test_irap_gate_has_safety_cap():
    """IRAP umuman kelmasa (GOP juda uzun) darvoza majburan ochiladi."""
    from vizora.sources.ezviz.decrypt import _IRAP_MAX_WAIT
    d = _dec()
    for _ in range(_IRAP_MAX_WAIT + 1):
        d._irap_gate(1, 2)
    assert d._seen_irap is True, "abadiy kutib qolmasligi kerak"
