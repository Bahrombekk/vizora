"""Iste'molchi API (v1) — snapshot va WebRTC qo'shimchalari.

Bu endpoint'lar TASHQI integratorlar uchun: ilgari snapshot va WHEP faqat admin
API'da bor edi (admin JWT bilan), ya'ni integratorlar ularni umuman ishlatolmasdi.
"""
import inspect
import uuid
from types import SimpleNamespace

from vizora.api import responses
from vizora.api.panel import cameras as admin_api
from vizora.api.integration import rest as v1
from vizora.core.config import settings
from vizora.services import camera_streams


def _paths() -> set[str]:
    return {r.path for r in v1.router.routes}


def _cam():
    cid = uuid.uuid4()
    return SimpleNamespace(id=cid, stream_path=f"cam/{cid}", has_sub=False, status="online")


def test_v1_has_snapshot_and_stream():
    """Integrator ishlatadigan yo'llar ro'yxatdan tushib qolmasin."""
    assert "/v1/cameras/{cid}/snapshot" in _paths()
    assert "/v1/cameras/{cid}/stream" in _paths()


def test_v1_snapshot_goes_through_scope_check():
    """Snapshot `_load()` orqali o'tishi SHART.

    `_load` scope'ni tekshiradi (ruxsatsiz kameraga 403). Uni chetlab o'tish
    mijozga BOSHQA mijozning kamerasidan kadr berib qo'yardi — bu API'dagi
    yagona jiddiy xavf, shuning uchun test bilan qulflangan."""
    src = inspect.getsource(v1.get_snapshot)
    assert "_load(db, cid, client)" in src


def test_v1_stream_returns_whep_url(monkeypatch):
    """WebRTC URL'i javobda bo'lsin (WHEP ayni stream token bilan ishlaydi).

    Busiz integrator tez yo'ldan (o'lchandi: 4.97s -> 1.85s) bexabar qoladi."""
    src = inspect.getsource(v1.get_stream)
    assert "camera_streams.start(" in src and "camera_streams.viewer_links(" in src

    monkeypatch.setattr(settings, "webrtc_enabled", True)
    links = camera_streams.viewer_links(_cam())
    assert links["whep_url"].startswith("/whep/") and links["token"] in links["whep_url"]
    monkeypatch.setattr(settings, "webrtc_enabled", False)   # o'chirilgan bo'lsa qaytarilmaydi
    assert "whep_url" not in camera_streams.viewer_links(_cam())


def test_webrtc_skips_hls_warmup(monkeypatch):
    """WebRTC'da HLS segment kutilmaydi; WS'da umuman kutilmaydi (hub o'zi kutadi)."""
    monkeypatch.setattr(settings, "webrtc_enabled", True)
    monkeypatch.setattr(settings, "ws_stream_enabled", True)
    assert camera_streams._ezviz_wait("webrtc") == {"warm_hls": False}
    assert camera_streams._ezviz_wait("hls") == {"warm_hls": True}
    assert camera_streams._ezviz_wait("ws") is None
    monkeypatch.setattr(settings, "webrtc_enabled", False)
    assert camera_streams._ezviz_wait("webrtc") == {"warm_hls": True}


def test_snapshot_glue_is_shared_not_duplicated():
    """Admin va v1 AYNI yordamchini chaqiradi (mantiq ikkiga bo'linib ketmasin)."""
    assert "snapshot.start_refresh(" in inspect.getsource(responses.snapshot_response)
    for fn in (v1.get_snapshot, admin_api.get_camera_snapshot):
        src = inspect.getsource(fn)
        assert "snapshot_response(" in src
        # Eski takror blok qaytib kelmasin
        assert "asyncio.create_task(snapshot.refresh(" not in src
        assert "get_cached(" not in src


def test_snapshot_disabled_returns_204_before_db():
    """Sozlamada o'chirilgan bo'lsa DB'ga bormasdan 204 — mijoz poll'ni to'xtatadi."""
    src = inspect.getsource(v1.get_snapshot)
    i_flag = src.index("ezviz_snapshot_enabled")
    i_load = src.index("_load(db, cid, client)")
    assert i_flag < i_load
