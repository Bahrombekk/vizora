"""NVENC sessiya chegarasi: ffmpeg o'lsa CPU'ga o'tish (publisher) + manager limiti."""
import asyncio
import types

import pytest

from vizora.core.config import settings
from vizora.media import ffmpeg_hw
from vizora.sources.ezviz import manager


@pytest.fixture(autouse=True)
def _reset_gpu_state(monkeypatch):
    monkeypatch.setattr(ffmpeg_hw, "_gpu_disabled_reason", None)
    monkeypatch.setattr(ffmpeg_hw, "_quick_deaths", 0)
    monkeypatch.setattr(ffmpeg_hw, "use_nvenc", lambda: True)
    yield


NVENC_LOG = """[h264_nvenc @ 0x5cc149799400] OpenEncodeSessionEx failed: incompatible client key (21): (no details)
[h264_nvenc @ 0x5cc149799400] No capable devices found
[vost#0:0/h264_nvenc @ 0x5cc1497a5100] Error while opening encoder - maybe incorrect parameters
"""


def test_marker_detected_disables_gpu_and_switches_encoder():
    assert ffmpeg_hw.encoder_name() == "h264_nvenc"
    assert ffmpeg_hw.note_encoder_death(1.2, stderr_text=NVENC_LOG) is True
    assert "OpenEncodeSessionEx failed" in (ffmpeg_hw.gpu_disabled() or "")
    assert ffmpeg_hw.encoder_name() == "libx264"
    assert "libx264" in ffmpeg_hw.encoder_args(2000)
    # Qayta chaqirish hech narsani o'zgartirmaydi
    assert ffmpeg_hw.note_encoder_death(0.5, stderr_text=NVENC_LOG) is False


def test_two_quick_deaths_without_marker_fall_back():
    assert ffmpeg_hw.note_encoder_death(1.0, stderr_text="") is False
    assert ffmpeg_hw.encoder_name() == "h264_nvenc"
    assert ffmpeg_hw.note_encoder_death(2.0, stderr_text="") is True
    assert ffmpeg_hw.encoder_name() == "libx264"


def test_long_lived_death_is_not_nvenc(monkeypatch):
    # 30s ishlagan ffmpeg o'ldi (bulut sababli) — GPU o'chirilmaydi, hisoblagich nollanadi
    assert ffmpeg_hw.note_encoder_death(1.0, stderr_text="") is False
    assert ffmpeg_hw.note_encoder_death(30.0, stderr_text="") is False
    assert ffmpeg_hw.note_encoder_death(1.0, stderr_text="") is False   # yana 1-chi tez o'lim
    assert ffmpeg_hw.gpu_disabled() is None


def test_cpu_mode_never_touches_gpu_state(monkeypatch):
    monkeypatch.setattr(ffmpeg_hw, "use_nvenc", lambda: False)
    assert ffmpeg_hw.note_encoder_death(0.1, stderr_text=NVENC_LOG) is False
    assert ffmpeg_hw.gpu_disabled() is None


class _Proc:
    def __init__(self, rc=None):
        self.returncode = rc


def test_manager_gpu_slot_limit(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {})
    monkeypatch.setattr(settings, "nvenc_max_sessions", 2)
    assert manager._gpu_slot_free() is True
    manager._publishers["a"] = manager._Publisher(_Proc(), "cam/a", gpu=True)
    manager._publishers["b"] = manager._Publisher(_Proc(), "cam/b", gpu=True)
    assert manager._gpu_publishers() == 2
    assert manager._gpu_slot_free() is False          # limit to'ldi -> CPU
    manager._publishers["c"] = manager._Publisher(_Proc(), "cam/c", gpu=False)
    assert manager._gpu_publishers() == 2             # CPU publisher hisoblanmaydi
    manager._publishers["a"].proc.returncode = 0      # biri o'ldi
    assert manager._gpu_slot_free() is True


def test_manager_gpu_slot_when_nvenc_unavailable(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {})
    monkeypatch.setattr(ffmpeg_hw, "use_nvenc", lambda: False)
    assert manager._gpu_slot_free() is False


def test_publisher_dead_only_for_existing_dead_publisher(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {})
    # Publisher YO'Q (RTSP kamera) -> False: kutishni davom ettiramiz
    assert manager.publisher_dead("cam/rtsp") is False
    manager._publishers["a"] = manager._Publisher(_Proc(), "cam/a")
    assert manager.publisher_dead("cam/a") is False      # tirik
    manager._publishers["a"].proc.returncode = 1
    assert manager.publisher_dead("cam/a") is True       # o'lgan -> tez xato


def test_touch_viewer_refreshes_idle_clock(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {})
    pub = manager._Publisher(_Proc(), "cam/x")
    pub.last_request = 0.0
    manager._publishers["x"] = pub
    manager.touch_viewer("cam/x")
    assert pub.last_request > 0.0
    manager.touch_viewer("cam/yoq")   # mavjud bo'lmagan path — xatosiz


def test_negative_nvenc_result_is_rechecked(monkeypatch, tmp_path):
    """GPU vaqtincha band bo'lgandagi "nvenc: false" bir soat/umrbod qolmasin —
    prod deploy'ida (2026-11-03) GPU bo'sh bo'lsa ham hamma kamera CPU'da kodlandi."""
    from vizora.core.config import settings
    from vizora.media import ffmpeg_hw as h

    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    monkeypatch.setattr(h, "_memo", None)
    results = iter([False, True])
    monkeypatch.setattr(h, "_probe_nvenc", lambda: next(results))
    t = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: t[0])
    wall = [5000.0]
    monkeypatch.setattr(h.time, "time", lambda: wall[0])
    assert h._nvenc_works() is False
    t[0] += 10; wall[0] += 10
    assert h._nvenc_works() is False          # qisqa muddat ichida — qayta sinamaydi
    t[0] += h._NEG_CACHE_TTL; wall[0] += h._NEG_CACHE_TTL
    assert h._nvenc_works() is True           # muddat o'tdi — qayta sinab GPU topildi
    t[0] += 100_000; wall[0] += 100
    assert h._nvenc_works() is True           # ijobiy natija eslab qolinadi
