"""Oqim avtorizatsiyasi — kameralararo izolyatsiya, admin sessiyasini bekor qilish
va stream token'ni cheksiz yangilab bo'lmasligi kafolati.

`authorize_http` / `authorize_ws` THE kirish darvozasi (/hls, /whep, /ws/stream).
"""
import re
from types import SimpleNamespace

from vizora.api.media import hls
from vizora.api.media.auth import StreamGrant, authorize_ws, is_admin
from vizora.api.media.auth import authorize_http as _authorize
from vizora.core.security import create_access_token
from vizora.media import stream
from tests.conftest import FAKE_USERS

_A = "cam/11111111-1111-1111-1111-111111111111"
_B = "cam/22222222-2222-2222-2222-222222222222"


def _req(*, cookie=None, token=None, bearer=None):
    """auth.authorize_http faqat .cookies/.query_params/.headers .get'ini ishlatadi."""
    return SimpleNamespace(
        cookies={"vz_stream": cookie} if cookie else {},
        query_params={"token": token} if token else {},
        headers={"authorization": f"Bearer {bearer}"} if bearer else {},
    )


async def test_stream_token_grants_only_its_camera():
    tok, _ = stream.make_stream_token(_A)
    assert await _authorize(_req(token=tok), _A) == StreamGrant(cookie=tok, token=tok)
    assert await _authorize(_req(token=tok), _B) is None       # boshqa kamera — RAD


async def test_admin_jwt_only_via_bearer_not_query():
    """Admin JWT URL'da (?token=) qabul qilinmaydi — access loglarga tushardi."""
    admin = create_access_token(subject="admin", role="admin")
    assert await _authorize(_req(token=admin), _A) is None
    # Ruxsat, cookie yangilanmaydi, playlist'ga admin JWT qo'yilmaydi (token=None).
    g = await _authorize(_req(bearer=admin), _A)
    assert (g.cookie, g.token) == ("", None)
    assert g.owner == {"own": "u:admin", "ver": 0}    # admin uchun mint qilinadigan token egasi
    assert (await _authorize(_req(bearer=admin), _B)).token is None
    assert await is_admin(admin) is True


async def test_admin_jwt_revoked_by_token_version():
    """Parol almashgach (token_version oshgach) eski admin JWT oqimga ham kirmaydi."""
    FAKE_USERS["boss"] = SimpleNamespace(role="admin", token_version=3)
    old = create_access_token(subject="boss", role="admin", ver=2)
    cur = create_access_token(subject="boss", role="admin", ver=3)
    assert await is_admin(old) is False
    assert await _authorize(_req(bearer=old), _A) is None
    assert await is_admin(cur) is True


async def test_deleted_user_jwt_rejected():
    ghost = create_access_token(subject="ghost", role="admin")
    assert await is_admin(ghost) is False


async def test_ws_admin_cookie_checks_db(monkeypatch):
    from vizora.api.media import auth as media_auth

    monkeypatch.setattr(media_auth, "origin_allowed", lambda h: True)
    FAKE_USERS["boss"] = SimpleNamespace(role="admin", token_version=1)

    def _ws(jwt):
        return SimpleNamespace(query_params={}, headers={}, cookies={"vz_admin": jwt})

    assert await authorize_ws(_ws(create_access_token(subject="boss", role="admin", ver=1)), _A) == "u:boss"
    assert await authorize_ws(_ws(create_access_token(subject="boss", role="admin", ver=0)), _A) is None


async def test_stream_token_is_not_admin():
    tok, _ = stream.make_stream_token(_A)
    assert await is_admin(tok) is False                       # typ=stream, role yo'q


async def test_valid_cookie_grants():
    tok, _ = stream.make_stream_token(_A)
    assert await _authorize(_req(cookie=tok), _A) == StreamGrant(cookie="", token=tok)
    assert await _authorize(_req(cookie=tok), _B) is None


async def test_bearer_header_path():
    tok, _ = stream.make_stream_token(_A)
    assert await _authorize(_req(bearer=tok), _A) == StreamGrant(cookie="", token=tok)
    assert await _authorize(_req(bearer=tok), _B) is None


async def test_no_credentials_denied():
    assert await _authorize(_req(), _A) is None


async def test_garbage_token_denied():
    assert await _authorize(_req(token="not-a-jwt"), _A) is None
    assert await is_admin("not-a-jwt") is False


async def test_master_playlist_does_not_renew_stream_token(monkeypatch):
    """master.m3u8 variantlariga YANGI token emas, kelgan tokenning o'zi qo'yiladi.

    Ilgari har so'rovda yangi 120 daqiqalik token mint qilinardi — eski token bilan
    master'ni davriy so'rab, o'chirilgan ApiClient ham oqimni abadiy ko'rardi."""
    async def _variant(_path):
        return "main_stream.m3u8"

    monkeypatch.setattr(hls, "_variant_name", _variant)
    tok, _ = stream.make_stream_token(_A)
    resp = await hls.hls_proxy(f"{_A}/master.m3u8", _req(token=tok))
    body = resp.body.decode()
    tokens = set(re.findall(r"token=([^&\s\"]+)", body))
    assert tokens == {tok}
