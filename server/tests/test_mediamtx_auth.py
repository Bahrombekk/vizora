"""MediaMTX ichki auth (audit 2026-11-03, 4-band): backend har yo'lda login yuboradi."""
import base64
from pathlib import Path

import pytest
import yaml

from vizora.core.config import settings
from vizora.media import mediamtx
from vizora.media.rtsp_reader import split_userinfo


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(settings, "mediamtx_user", "vzapp")
    monkeypatch.setattr(settings, "mediamtx_password", "p@ss/w:rd")
    monkeypatch.setattr(settings, "mediamtx_rtsp_host", "mediamtx")


def test_rtsp_url_carries_encoded_creds(creds):
    assert mediamtx.rtsp_url("cam/x/raw") == "rtsp://vzapp:p%40ss%2Fw%3Ard@mediamtx:8554/cam/x/raw"


def test_no_password_no_creds(monkeypatch):
    monkeypatch.setattr(settings, "mediamtx_password", "")
    monkeypatch.setattr(settings, "mediamtx_rtsp_host", "mediamtx")
    assert mediamtx.api_auth() is None
    assert mediamtx.rtsp_url("cam/x") == "rtsp://mediamtx:8554/cam/x"


def test_with_creds_only_for_mediamtx_host(creds):
    isup = "rtsp://mediamtx:8554/isup/DEV1/1"
    assert mediamtx.with_creds(isup).startswith("rtsp://vzapp:")
    cam = "rtsp://admin:x@192.168.1.64/Streaming/Channels/101"
    assert mediamtx.with_creds(cam) == cam                         # kamera — tegilmaydi
    other = "rtsp://10.0.0.5/live"
    assert mediamtx.with_creds(other) == other


def test_isup_path_config_pulls_with_creds(creds):
    cfg = mediamtx.build_path_config("rtsp://mediamtx:8554/isup/DEV1/1", transcode=False)
    assert cfg["source"].startswith("rtsp://vzapp:")
    cfg = mediamtx.build_path_config("rtsp://mediamtx:8554/isup/DEV1/1", transcode=True)
    assert "vzapp:" in cfg["runOnDemand"]


def test_split_userinfo_moves_creds_to_header():
    url, auth = split_userinfo("rtsp://vzapp:p%40ss@mediamtx:8554/cam/x")
    assert url == "rtsp://mediamtx:8554/cam/x"
    assert auth == "Basic " + base64.b64encode(b"vzapp:p@ss").decode()
    assert split_userinfo("rtsp://mediamtx:8554/a") == ("rtsp://mediamtx:8554/a", None)


def test_mediamtx_yml_has_no_open_api():
    """`any` foydalanuvchi faqat localhost'dan publish qila oladi — API/o'qish yo'q."""
    yml = Path(__file__).parents[2] / "mediamtx.yml"
    if not yml.exists():
        pytest.skip("mediamtx.yml faqat to'liq repo bilan (backend alohida mount qilingan)")
    cfg = yaml.safe_load(yml.read_text(encoding="utf-8"))
    users = cfg["authInternalUsers"]
    anyu = [u for u in users if u["user"] == "any"]
    assert len(anyu) == 1
    assert {p["action"] for p in anyu[0]["permissions"]} == {"publish"}
    assert set(anyu[0]["ips"]) <= {"127.0.0.1/32", "::1/128"}
    # compose MTX_AUTHINTERNALUSERS_1/2_PASS shu indekslarga tayanadi
    assert users[1]["user"] == "vzapp" and users[2]["user"] == "vzisup"
    assert {p["action"] for p in users[2]["permissions"]} == {"publish"}
    assert "~^isup/" in cfg["paths"]
