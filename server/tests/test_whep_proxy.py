"""WHEP (WebRTC) proxy yo'l tekshiruvi va ruxsati.

Nega muhim: MediaMTX (auth=any) tashqariga ochilmaydi — brauzer FAQAT shu proxy
orqali o'tadi. Yo'l qat'iy tekshirilishi kerak, aks holda mijoz xohlagan
MediaMTX path'iga (masalan boshqa kameraga) kirib ketishi mumkin.
"""
import pytest

from vizora.media.stream import make_stream_token
from vizora.api.media.whep import _parse

CAM = "cam/0f3a1c9e-2b7d-4c1a-9e8f-1234567890ab"
SESS = "74d9f938-0e45-4826-9a56-01f460177572"


async def _fake_guard(path, request):
    return None, "cam/x/whep/s"


def test_offer_path():
    assert _parse(f"{CAM}/whep") == (CAM, f"{CAM}/whep")


def test_session_path():
    """PATCH/DELETE uchun sessiya yo'li — ruxsat KAMERA path'i bo'yicha beriladi."""
    assert _parse(f"{CAM}/whep/{SESS}") == (CAM, f"{CAM}/whep/{SESS}")


def test_sub_variant_path():
    """SD (sub) variant ham asosiy kamera tokeni ostida ruxsat oladi."""
    assert _parse(f"{CAM}/sub/whep") == (CAM, f"{CAM}/sub/whep")


@pytest.mark.parametrize("bad", [
    "cam/not-a-uuid/whep",                     # UUID emas
    f"{CAM}",                                  # /whep yo'q
    f"{CAM}/whep/../../secret",                # traversal
    f"{CAM}/whep/{SESS}/extra",                # ortiqcha segment
    "other/0f3a1c9e-2b7d-4c1a-9e8f-1234567890ab/whep",  # cam/ prefiksi emas
    f"{CAM}/whep/not-a-uuid",                  # sessiya UUID emas
    "",                                        # bo'sh
])
def test_rejects_bad_paths(bad):
    assert _parse(bad) is None


def test_token_is_bound_to_camera():
    """Bir kameraning tokeni BOSHQA kamerani ochmasligi kerak."""
    from vizora.media.stream import verify_stream_token
    token, _ = make_stream_token(CAM)
    assert verify_stream_token(token, CAM) is True
    other = "cam/ffffffff-2b7d-4c1a-9e8f-1234567890ab"
    assert verify_stream_token(token, other) is False


# --- Idempotent teardown (brauzerda qizil 404 bo'lmasin) ---

@pytest.mark.asyncio
async def test_delete_maps_404_to_204(monkeypatch):
    """Allaqachon o'chgan sessiyani DELETE qilish = MUVAFFAQIYAT (204, 404 emas).

    WebRTC UDP porti yopiq muhitda MediaMTX sessiyani ICE vaqti tugashi bilan
    o'zi o'chiradi. Brauzer keyin DELETE yuborsa MediaMTX 404 beradi — bu
    DevTools'да keraksiz qizil xato ko'rsatardi. Teardown maqsadi bajarilgan."""
    import httpx
    from fastapi import Request
    from vizora.api.media import whep as whep_proxy

    class _Resp:
        def __init__(self, code): self.status_code = code; self.content = b""; self.headers = {}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def delete(self, url): return _Resp(404)   # sessiya allaqachon yo'q

    monkeypatch.setattr(whep_proxy, "_guard", _fake_guard)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    req = Request({"type": "http", "method": "DELETE", "headers": [], "query_string": b""})
    resp = await whep_proxy.whep_delete("cam/x/whep/s", req)
    assert resp.status_code == 204, "404 -> 204 ga aylanishi kerak"


@pytest.mark.asyncio
async def test_patch_maps_404_to_204(monkeypatch):
    """O'lgan sessiyaga trickle ICE (PATCH) 404 o'rniga jimgina 204."""
    import httpx
    from fastapi import Request
    from vizora.api.media import whep as whep_proxy

    class _Resp:
        def __init__(self, code): self.status_code = code; self.content = b""; self.headers = {}
    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def patch(self, url, content=None, headers=None): return _Resp(404)

    monkeypatch.setattr(whep_proxy, "_guard", _fake_guard)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    req = Request({"type": "http", "method": "PATCH", "headers": [], "query_string": b""})

    async def _body(): return b""
    req._body = b""
    resp = await whep_proxy.whep_patch("cam/x/whep/s", req)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_oversized_offer_rejected(monkeypatch):
    """SDP taklifi 64 KB dan katta bo'lsa MediaMTX'ga uzatilmaydi — 413 (audit 2026-11-03)."""
    from fastapi import Request
    from vizora.api.media import whep as whep_proxy

    monkeypatch.setattr(whep_proxy, "_guard", _fake_guard)
    req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b""})
    req._body = b"v=0\r\n" * 20000
    resp = await whep_proxy.whep_offer("cam/x/whep", req)
    assert resp.status_code == 413
