"""Kanal darvozasi: bulut qurilma online bo'lsa ham, MAVJUD BO'LMAGAN kanal
"offline" bo'lishi kerak (aks holda kamera "online" ko'rinib, ochilganda qotardi).
"""
import json

import pytest

from vizora.sources.ezviz import vtm_cache


@pytest.fixture
def cache(tmp_path, monkeypatch):
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    # pagelist'ni to'g'ridan yozamiz (bulut javobiga o'xshash tuzilish)
    data = {"deviceInfos": [
        {"deviceSerial": "NVR1", "resourceInfos": [
            {"deviceSerial": "NVR1", "channelNo": 2},
            {"deviceSerial": "NVR1", "channelNo": 8},
            {"deviceSerial": "NVR1", "channelNo": 9},
        ]},
        {"deviceSerial": "SOLO1", "channelNo": 0},   # bir kanalli qurilma
    ]}
    vtm_cache.store("acc1", "pagelist", data)
    return tmp_path


def test_missing_channel_on_multichannel_device(cache):
    """NVR'da 2,8,9 bor — kanal 1 YO'Q -> missing=True."""
    assert vtm_cache.channel_missing("acc1", "NVR1", 1) is True


def test_present_channel_is_not_missing(cache):
    for ch in (2, 8, 9):
        assert vtm_cache.channel_missing("acc1", "NVR1", ch) is False


def test_single_channel_device_never_flagged(cache):
    """Bir kanalli qurilma (kanal to'plami bo'sh/0) hech qachon 'yo'q' bo'lmaydi —
    noto'g'ri offline bermaslik uchun."""
    assert vtm_cache.channel_missing("acc1", "SOLO1", 1) is False


def test_unknown_serial_not_flagged(cache):
    """Ro'yxatда yo'q seriya — ishonchsiz, holatni o'zgartirmaymiz."""
    assert vtm_cache.channel_missing("acc1", "BOSHQA", 1) is False


def test_no_cache_returns_false(tmp_path, monkeypatch):
    """Kesh yo'q bo'lsa hech qachon 'yo'q' demaymiz (cold start'da false offline yo'q)."""
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    assert vtm_cache.channel_missing("yoq", "NVR1", 1) is False
    assert vtm_cache.available_channels("yoq") is None


def test_available_channels_shape(cache):
    m = vtm_cache.available_channels("acc1")
    assert m["NVR1"] == {2, 8, 9}
    assert m["SOLO1"] == {0}


def test_probe_gate_only_downgrades(monkeypatch):
    """Probe darvozasi FAQAT online->offline qiladi (offline->online EMAS).

    Manba kodda: gate `if online and channel_missing(...)` ostida — offline
    kamera hech qachon shu tufayli online bo'lmaydi."""
    import inspect
    from vizora.services import probe
    src = inspect.getsource(probe)
    assert "if online and vtm_cache.channel_missing(" in src
