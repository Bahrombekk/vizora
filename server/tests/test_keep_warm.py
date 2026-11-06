"""Kamera "doim issiq" (Camera.keep_warm) — tomoshabinsiz ham oqim olinib turadi."""
import asyncio
import time
from types import SimpleNamespace

from vizora.core.config import settings
from vizora.media import mediamtx
from vizora.sources import isup
from vizora.sources.ezviz import manager as mgr


def test_rtsp_path_pulled_continuously_when_warm():
    """RTSP: on-demand emas, MediaMTX manbani doimiy tortadi."""
    assert mediamtx.build_path_config("rtsp://cam/1", False)["sourceOnDemand"] is True
    assert mediamtx.build_path_config("rtsp://cam/1", False, always_on=True)["sourceOnDemand"] is False


def test_transcoded_path_runs_on_init_when_warm():
    """Transkodli path: runOnDemand o'rniga runOnInit (ffmpeg doim ishlaydi, o'lsa qayta)."""
    cold = mediamtx.build_path_config("rtsp://cam/1", True)
    warm = mediamtx.build_path_config("rtsp://cam/1", True, always_on=True)
    assert "runOnDemand" in cold and "runOnInit" not in cold
    assert "runOnInit" in warm and warm["runOnInitRestart"] is True and "runOnDemand" not in warm
    assert "-forced-idr" in warm["runOnInit"] or "libx264" in warm["runOnInit"]


def test_isup_main_stream_always_wanted_when_warm(monkeypatch):
    monkeypatch.setattr(settings, "isup_main_idle_seconds", 60)
    monkeypatch.setattr(isup, "_hot", {})
    cold = SimpleNamespace(isup_device_id="D1", isup_channel=1, codec="h264", keep_warm=False)
    warm = SimpleNamespace(isup_device_id="D2", isup_channel=1, codec="h264", keep_warm=True)
    mains = [x for x in isup.want_lines([cold, warm], now=1000.0) if x.endswith(f" {isup.MAIN}")]
    assert mains == [f"D2 1 {isup.MAIN}"]


def test_reaper_keeps_panel_warm_cameras(monkeypatch):
    """Panelda yoqilgan kamera publisher'i tomoshabinsiz ham to'xtatilmaydi."""
    monkeypatch.setattr(settings, "always_warm_camera_ids", "")
    monkeypatch.setattr(settings, "ezviz_transcode_idle_seconds", 60)
    stopped = []

    async def _readers():
        return {}

    async def _stop(cid):
        stopped.append(cid)

    monkeypatch.setattr(mgr, "_path_readers", _readers)
    monkeypatch.setattr(mgr, "stop", _stop)
    monkeypatch.setattr(mgr, "_keep_warm_ids", {"warm"})
    old = time.monotonic() - 3600
    proc = SimpleNamespace(returncode=None)
    mk = lambda p: SimpleNamespace(proc=proc, alive=True, stream_path=p, provider="hik",  # noqa: E731
                                   raw_only=False, last_request=old)
    monkeypatch.setattr(mgr, "_publishers", {"warm": mk("cam/warm"), "cold": mk("cam/cold")})
    asyncio.run(mgr._reap_once())
    assert stopped == ["cold"]
