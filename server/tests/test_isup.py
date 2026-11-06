"""ISUP 5.0 manba turi: qaysi oqim ochiq turishi (want), URL'lar, validatsiya.

Asosiy g'oya: substream har ISUP kamera uchun doim ochiq (arzon), asosiy oqim esa
faqat ko'rilganda — 100 kamerada 24/7 asosiy oqim 420 Mbit/s bo'lardi.
"""
import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from vizora.core.config import settings
from vizora.schemas.camera import CameraCreate, CameraUpdate
from vizora.sources import isup


def _cam(dev="TCG406TEST", ch=1, codec="h264"):
    return SimpleNamespace(isup_device_id=dev, isup_channel=ch, codec=codec)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    isup._hot.clear()
    monkeypatch.setattr(settings, "isup_substream", True)
    monkeypatch.setattr(settings, "isup_main_idle_seconds", 60)
    yield
    isup._hot.clear()


def test_idle_camera_wants_only_substream():
    assert isup.want_lines([_cam()], now=1000.0) == ["TCG406TEST 1 1"]


def test_unknown_codec_keeps_main_open_for_probe():
    """Kodek aniqlanmaguncha asosiy oqim ochiq — aks holda HEVC kamera transkodsiz
    qolib, HLS/WebRTC ochilmasdi."""
    assert "TCG406TEST 1 0" in isup.want_lines([_cam(codec="auto")], now=1000.0)


def test_touched_camera_wants_main_until_idle():
    isup._hot[("TCG406TEST", 1)] = 1000.0
    assert "TCG406TEST 1 0" in isup.want_lines([_cam()], now=1030.0)
    assert "TCG406TEST 1 0" not in isup.want_lines([_cam()], now=1061.0)


def test_mediamtx_readers_keep_main_alive():
    """Tomoshabin uzoq ko'rsa ham asosiy oqim yopilmasin — o'quvchi bor ekan, 'issiq'."""
    readers = {"isup/TCG406TEST/1": 1}
    isup._hot[("TCG406TEST", 1)] = 0.0  # eski touch
    assert "TCG406TEST 1 0" in isup.want_lines([_cam()], now=5000.0, readers=readers)


def test_idle_zero_means_main_always(monkeypatch):
    monkeypatch.setattr(settings, "isup_main_idle_seconds", 0)
    assert isup.want_lines([_cam()], now=1.0) == ["TCG406TEST 1 0", "TCG406TEST 1 1"]


def test_substream_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "isup_substream", False)
    assert isup.want_lines([_cam()], now=1.0) == []


def test_unsafe_device_id_never_reaches_bridge():
    """Device ID bridge'da shell buyrug'i va path nomiga tushadi."""
    cams = [_cam(dev="x; rm -rf /"), _cam(dev="../etc"), _cam(dev=None), _cam(dev="OK-1_a", ch=3)]
    assert isup.want_lines(cams, now=1.0) == ["OK-1_a 3 1"]


def test_paths_and_urls(monkeypatch):
    monkeypatch.setattr(settings, "mediamtx_rtsp_host", "mediamtx")
    assert isup.path_for("D1", 2) == "isup/D1/2"
    assert isup.path_for("D1", 2, sub=True) == "isup/D1/2/sub"
    assert isup.rtsp_url("D1", 2) == "rtsp://mediamtx:8554/isup/D1/2"


def test_fill_urls_points_camera_at_isup_paths(monkeypatch):
    from vizora.core import crypto

    monkeypatch.setattr(settings, "mediamtx_rtsp_host", "mediamtx")
    cam = SimpleNamespace(isup_device_id="D1", isup_channel=4, rtsp_url_enc=None, rtsp_sub_url_enc=None)
    isup.fill_urls(cam)
    assert crypto.decrypt(cam.rtsp_url_enc) == "rtsp://mediamtx:8554/isup/D1/4"
    assert crypto.decrypt(cam.rtsp_sub_url_enc) == "rtsp://mediamtx:8554/isup/D1/4/sub"


def test_device_online_unknown_when_bridge_down():
    assert isup.device_online(None, "D1") is None
    assert isup.device_online({}, "D1") is False
    assert isup.device_online({"D1": {}}, "D1") is True


def test_schema_requires_device_id_and_rejects_unsafe():
    with pytest.raises(ValidationError):
        CameraCreate(name="x", source_type="isup")
    with pytest.raises(ValidationError):
        CameraCreate(name="x", source_type="isup", isup_device_id="a b")
    with pytest.raises(ValidationError):
        CameraUpdate(isup_device_id="a/b")
    c = CameraCreate(name="x", source_type="isup", isup_device_id="TCG406TEST", isup_channel=2)
    assert c.isup_channel == 2 and c.rtsp_url is None


def test_probe_treats_isup_by_registration_not_ffprobe():
    """Asosiy oqim uxlab yotgan ISUP kamerani ffprobe qilsak — har biri oflayn chiqardi."""
    from vizora.services import probe

    src = inspect.getsource(probe.probe_once)
    assert "isup.device_online(isup_state" in src
    assert "elif snap.is_isup:" in src


# --- Kalitlar: hudud darajasi + qurilma kaliti + zaxira -----------------------
def _dev(dev="D1", zone=None, blocked=False, key=None):
    return SimpleNamespace(device_id=dev, zone_id=zone, blocked=blocked, key_plain=key)


def _fac(fid, key_id=None, prefix=None):
    return SimpleNamespace(id=fid, isup_key_id=key_id, isup_prefix=prefix)


@pytest.fixture
def keys_env(monkeypatch):
    monkeypatch.setattr(settings, "isup_allow_default_key", True)
    monkeypatch.setattr(settings, "isup_default_key", "Default12345")
    facs = {"F1": _fac("F1", "K1", "SCH1-"), "F2": _fac("F2", "K1"), "F3": _fac("F3", None, "SCH3-")}
    keys = {"K1": "HududKalit01"}
    return facs, keys


def test_resolve_order(keys_env):
    facs, keys = keys_env
    assert isup.resolve_key(_dev(blocked=True, key="OwnKey123"), facs, keys) == (None, "blocked")
    assert isup.resolve_key(_dev(zone="F1", key="OwnKey123"), facs, keys) == ("OwnKey123", "device")
    assert isup.resolve_key(_dev(zone="F2"), facs, keys) == ("HududKalit01", "zone")  # bitta kalit 2 hududda
    assert isup.resolve_key(_dev(zone="F3"), facs, keys) == ("Default12345", "default")


def test_resolve_without_default(keys_env, monkeypatch):
    facs, keys = keys_env
    monkeypatch.setattr(settings, "isup_allow_default_key", False)
    assert isup.resolve_key(_dev(zone="F3"), facs, keys) == (None, "none")


def test_key_lines_registered_devices_fully_resolved(keys_env):
    """Ro'yxatdagi qurilma kaliti backend'da hal qilinadi — begona hudud prefiksiga
    tushib qolmasin (prefiks faqat ro'yxatda yo'q qurilmalar uchun)."""
    facs, keys = keys_env
    lines = isup.key_lines(
        [_dev("SCH1-9", zone="F3"), _dev("B", blocked=True), _dev("bad id")], facs, keys)
    assert "dev SCH1-9 Default12345" in lines          # F3 kalitsiz -> zaxira, F1 prefiksi EMAS
    assert "block B" in lines
    assert not any("bad id" in x for x in lines)
    assert "prefix SCH1- HududKalit01" in lines
    assert not any(x.startswith("prefix SCH3-") for x in lines)  # kalitsiz hudud prefiksi yuborilmaydi
    assert lines[-1] == "default on"


def test_key_lines_blocks_keyless_when_default_off(keys_env, monkeypatch):
    facs, keys = keys_env
    monkeypatch.setattr(settings, "isup_allow_default_key", False)
    lines = isup.key_lines([_dev("X", zone="F3")], facs, keys)
    assert "block X" in lines and lines[-1] == "default off"


def test_generated_key_is_typeable():
    k = isup.generate_key()
    assert isup.KEY_RE.match(k) and len(k) == 16
    assert not set(k) & set("0O1lI")


def test_assigned_zone_key_beats_zone_default(keys_env):
    """Hudud ichida ko'p kalit: qurilmaga tanlangan kalit (key_id) hudud standartidan ustun,
    lekin qurilmaning eski o'z kaliti (key_enc) undan ham ustun."""
    facs, keys = keys_env
    keys = {**keys, "K2": "IchkiKalit002"}
    d = _dev(zone="F1")
    d.key_id = "K2"
    assert isup.resolve_key(d, facs, keys) == ("IchkiKalit002", "assigned")
    d.key_plain = "OwnKey123"
    assert isup.resolve_key(d, facs, keys) == ("OwnKey123", "device")
    d.key_plain, d.key_id = None, "YOQ"   # o'chirilgan kalit -> hudud standartiga qaytadi
    assert isup.resolve_key(d, facs, keys) == ("HududKalit01", "zone")


def test_slot_device_ids_unique_and_safe():
    """Ulanish ma'lumotlari: noyob, xavfsiz Device ID — band bo'lganlari o'tkaziladi."""
    f = SimpleNamespace(isup_prefix=None, code="B 001/x")
    base = isup.slot_base(f)
    assert base == "B001X"
    ids = isup.next_device_ids(base, {"B001X-002"}, 3)
    assert ids == ["B001X-001", "B001X-003", "B001X-004"]
    assert all(isup.DEVICE_ID_RE.match(i) for i in ids)
    assert isup.slot_base(SimpleNamespace(isup_prefix="SCH1-", code="z")) == "SCH1"


def test_auto_channels_for_nvr_and_camera():
    """NVR — har IP kanal uchun kamera (nomi bilan); oddiy kamera — 1-kanal; ro'yxat
    hali kelmagan bo'lsa kutiladi (aks holda NVR'da faqat 1-kanal ochilib qolardi)."""
    assert isup.auto_channels({"info": "pending", "online_seconds": 3}) is None
    assert isup.auto_channels({"info": "pending", "online_seconds": 60}) == [(1, "")]
    nvr = {"info": "ok", "ip_channels": [{"id": 2, "name": "Ombor"}, {"id": 1, "name": "Kirish "}, {"id": 0}]}
    assert isup.auto_channels(nvr) == [(1, "Kirish"), (2, "Ombor")]
    assert isup.auto_channels({"info": "ok", "channels_total": 4, "start_channel": 1}) == [(1, ""), (2, ""), (3, ""), (4, "")]
    assert isup.auto_channels({"info": "ok", "channels_total": 1}) == [(1, "")]
    assert isup.auto_channels({"info": "failed"}) == [(1, "")]


def test_codec_probe_capped_for_big_nvr():
    """16 kanalli NVR ulanganda kodek aniqlash uchun hammasi birdan ochilmaydi."""
    cams = [_cam(dev="NVR1", ch=i, codec="auto") for i in range(1, 17)]
    mains = [x for x in isup.want_lines(cams, now=1.0) if x.endswith(" 0")]
    assert len(mains) == isup.CODEC_PROBE_MAX


def test_codec_probe_window_rotates():
    """Oflayn kanal navbatni egallab qolmasin — har daqiqada boshqa kanallar sinaladi."""
    cams = [_cam(dev="NVR1", ch=i, codec="auto") for i in range(1, 17)]
    a = {x for x in isup.want_lines(cams, now=0.0) if x.endswith(" 0")}
    b = {x for x in isup.want_lines(cams, now=61.0) if x.endswith(" 0")}
    assert len(a) == len(b) == isup.CODEC_PROBE_MAX and a != b
