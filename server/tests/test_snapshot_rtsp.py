"""RTSP (simli) kameralar uchun ham SNAPSHOT — avval faqat EZVIZ'da bor edi.

Nega: grid'da online RTSP kamera (masalan "SCH1 kamera", "SHCH-1") jonli oqim
ochilguncha qora kvadrat ko'rsatardi — endpoint `is_ezviz` bo'lmasa 404 berardi,
grid karta esa faqat EZVIZ uchun snapshot so'rardi. Endi RTSP uchun kadr
kameraning O'ZIDAN (`_grab_from_rtsp`) bir martalik ffmpeg bilan olinadi.
"""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from vizora.services import snapshot


def test_refresh_uses_rtsp_grab_for_wired_camera():
    """`refresh` acc=None + cam.rtsp_url bo'lganda `_grab_from_rtsp` chaqirsin."""
    src = inspect.getsource(snapshot.refresh)
    assert "_grab_from_rtsp" in src
    # RTSP grab bulut fallback'idan OLDIN va acc'ga bog'liq emas
    assert 'getattr(cam, "rtsp_url", None)' in src
    # bulut yo'li endi acc is None bo'lganda o'tkazib yuborilsin
    assert "acc is not None" in src


def test_start_refresh_handles_rtsp_source():
    """RTSP kamera (is_ezviz False) uchun `start_refresh` rtsp_url deshifrlab
    refresh'ni ishga tushirsin — EZVIZ bo'lmagani uchun darrov qaytmasин."""
    src = inspect.getsource(snapshot.start_refresh)
    assert 'if not getattr(cam, "is_ezviz", False):' in src
    assert "rtsp_url_enc" in src
    assert "crypto.decrypt(url_enc)" in src
    # ISUP: asosiy oqim uxlab yotadi — kadr doim ochiq substream'dan olinadi
    assert "rtsp_sub_url_enc" in src


@pytest.mark.asyncio
async def test_rtsp_refresh_caches_grabbed_frame(monkeypatch, tmp_path):
    """`refresh` (acc=None) RTSP grab qaytargan JPEG'ni keshlashi kerak."""
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_snapshot_enabled", True)
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    snapshot._cache.clear()

    fake_jpeg = b"\xff\xd8\xff\xe0rtsp-frame"

    async def _fake_stream(_p):
        return None            # sovuq: MediaMTX'da oqim yo'q

    async def _fake_rtsp(url):
        assert url == "rtsp://cam/live"
        return fake_jpeg

    monkeypatch.setattr(snapshot, "_grab_from_stream", _fake_stream)
    monkeypatch.setattr(snapshot, "_grab_from_rtsp", _fake_rtsp)

    cam = SimpleNamespace(id="cam-1", stream_path="cam/x", rtsp_url="rtsp://cam/live")
    ok = await snapshot.refresh(cam, None, force=True)
    assert ok is True
    assert snapshot.get_cached("cam-1") == fake_jpeg


@pytest.mark.asyncio
async def test_rtsp_grab_scales_down(monkeypatch):
    """`_grab_from_rtsp` ffmpeg buyrug'ida kenglikni cheklovchi scale bo'lsin —
    to'liq 4MP sensor kadri (~900KB) poster uchun shart emas."""
    captured = {}

    class _P:
        returncode = 0

        async def communicate(self):
            return b"\xff\xd8ok", b""

    async def _fake_exec(*args, **kw):
        captured["args"] = args
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    out = await snapshot._grab_from_rtsp("rtsp://cam/live")
    assert out == b"\xff\xd8ok"
    joined = " ".join(captured["args"])
    assert "scale=" in joined and "1280" in joined
    assert "-rtsp_transport" in captured["args"] and "tcp" in captured["args"]
