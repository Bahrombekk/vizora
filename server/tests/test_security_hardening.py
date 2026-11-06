"""Xavfsizlik auditi (2026-10-02) tuzatishlari — qaytib kelmasin.

Har test bitta topilmaga mos: buyruq in'ektsiyasi (RTSP URL -> MediaMTX runOnDemand),
SSRF (ffprobe/ffmpeg protokollari), EZVIZ parolini begona regionga o'g'irlash, CSV
formula in'ektsiyasi, token turi/versiyasi, CSRF Origin, WS navbat bayt chegarasi.
"""
import inspect

import pytest
from pydantic import ValidationError

from vizora.schemas.camera import CameraCreate, CameraTestRequest, CameraUpdate, check_rtsp_url
from vizora.schemas.ezviz_account import EzvizAccountCreate, EzvizAccountUpdate
from vizora.core import security
from vizora.media import mediamtx, stream


@pytest.mark.parametrize("url", [
    'rtsp://a" -y -f image2 /tmp/p.jpg -i "rtsp://b',   # argument in'ektsiyasi
    "rtsp://u:$HOME@h/x",                                # MediaMTX $VAR kengaytmasi
    "rtsp://h/x`id`",
    "rtsp://h/x y",
    "file:///etc/passwd",                                # lokal fayl
    "http://mediamtx:9997/v3/config/global/get",         # SSRF
    "concat:rtsp://a|rtsp://b",
])
def test_bad_rtsp_urls_rejected(url):
    with pytest.raises(ValueError):
        check_rtsp_url(url)
    with pytest.raises(ValidationError):
        CameraTestRequest(rtsp_url=url)
    with pytest.raises(ValidationError):
        CameraCreate(name="x", rtsp_url=url)


def test_good_rtsp_urls_pass():
    for u in ("rtsp://admin:p%24ss@10.0.0.5:554/Streaming/Channels/101",
              "rtsps://cam.local/live", "rtsp://mediamtx:8554/isup/D1/1/sub"):
        assert check_rtsp_url(u) == u
    assert CameraUpdate(rtsp_sub_url="").rtsp_sub_url == ""   # "" = sub-stream'ni o'chirish


def test_mediamtx_refuses_bad_url_even_from_db():
    with pytest.raises(ValueError):
        mediamtx.build_path_config('rtsp://a" -y -i "rtsp://b', True)
    cmd = mediamtx.build_path_config("rtsp://10.0.0.5/x", True)["runOnDemand"]
    assert "-protocol_whitelist" in cmd and '-i "rtsp://10.0.0.5/x"' in cmd


def test_ffprobe_and_snapshot_whitelist_protocols():
    from vizora.services import probe
    from vizora.services import snapshot

    assert "-protocol_whitelist" in inspect.getsource(probe.probe_info)
    assert inspect.getsource(snapshot._grab_from_rtsp).count("-protocol_whitelist") == 1


def test_ezviz_region_only_cloud_domains():
    with pytest.raises(ValidationError):
        EzvizAccountCreate(label="x", account="a", password="p", region="attacker.example")
    with pytest.raises(ValidationError):
        EzvizAccountUpdate(region="169.254.169.254")
    assert EzvizAccountUpdate(region="ApiIsgp.EzvizLife.com").region == "apiisgp.ezvizlife.com"


def test_ezviz_account_writes_admin_only():
    from vizora.api.panel import ezviz_accounts as m

    for fn in (m.create_account, m.update_account, m.delete_account):
        assert "_: AdminOnly" in inspect.getsource(fn)


def test_csv_formula_cells_neutralised():
    from vizora.api.panel.cameras import _csv_safe

    for v in ("=HYPERLINK(\"https://evil\")", "+1", "-1+2", "@SUM(A1)", "\tx", "\rx"):
        assert _csv_safe(v).startswith("'")
    assert _csv_safe("Kirish darvozasi") == "Kirish darvozasi"
    assert _csv_safe(None) == ""


def test_stream_token_is_not_an_admin_token():
    tok, _ = stream.make_stream_token("cam/11111111-1111-1111-1111-111111111111")
    assert security.decode_access_token(tok) is None
    adm = security.create_access_token("admin", "admin", ver=3)
    p = security.decode_access_token(adm)
    assert p["typ"] == "access" and p["ver"] == 3


async def test_revoked_token_version_rejected():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from vizora.api.deps import get_current_user
    from tests.conftest import FAKE_USERS

    FAKE_USERS["admin"] = SimpleNamespace(role="admin", token_version=2)
    old = security.create_access_token("admin", "admin", ver=1)
    with pytest.raises(HTTPException) as ei:
        await get_current_user(SimpleNamespace(cookies={"vz_admin": old}), token=None)
    assert ei.value.status_code == 401
    ok = security.create_access_token("admin", "operator", ver=2)   # rol tokendan EMAS, bazadan
    user = await get_current_user(SimpleNamespace(cookies={"vz_admin": ok}), token=None)
    assert user.role == "admin"


def test_origin_check():
    from vizora.api.deps import origin_allowed

    assert origin_allowed({})                                                  # brauzer emas
    assert origin_allowed({"origin": "https://panel.uz", "host": "panel.uz"})
    assert origin_allowed({"origin": "https://panel.uz", "host": "app:8000", "x-forwarded-host": "panel.uz"})
    assert not origin_allowed({"origin": "https://evil.panel.uz", "host": "panel.uz"})
    assert not origin_allowed({"origin": "null", "host": "panel.uz"})


def test_ws_subscriber_byte_cap():
    from vizora.media import ws_stream

    sub = ws_stream.Subscriber()
    big = b"\x00" * (5 * 1024 * 1024)
    sub.push(big, key=True)
    sub.push(big)
    sub.push(big)                      # 15 MB > 12 MB -> navbat bo'shatiladi
    assert sub.qbytes <= ws_stream._QUEUE_MAX_BYTES
    assert sub.dropped >= 1


def test_password_max_bcrypt_limit():
    from vizora.schemas.auth import PasswordChange

    with pytest.raises(ValidationError):
        PasswordChange(current_password="x", new_password="a" * 73)


def test_origin_check_when_proxy_strips_port():
    """nginx `$host` portni olib tashlaydi — panelning o'z so'rovi rad etilmasin."""
    from vizora.api.deps import origin_allowed

    assert origin_allowed({"origin": "http://localhost:8080", "host": "localhost"})
    assert origin_allowed({"origin": "http://localhost:8080", "host": "localhost:8080"})
    assert not origin_allowed({"origin": "http://evil:8080", "host": "localhost"})
    assert not origin_allowed({"origin": "http://localhost:9999", "host": "localhost:8080"})
