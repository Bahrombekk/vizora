"""Stream token egasi (audit 2026-11-03, 10-band).

* token kim uchun berilgani (`own`) bazada tekshiriladi: mijoz o'chirilsa, kaliti
  almashtirilsa yoki admin paroli o'zgarsa token 120 daqiqa kutmasdan o'ladi;
* WS ulanishlar chegarasi token emas, EGA bo'yicha — yangi token olib chegarani
  aylanib o'tib bo'lmaydi;
* ochiq WS ham ega bekor qilinganda uziladi.
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vizora.api.media import auth as media_auth
from vizora.api.media import ws as ws_mod
from vizora.api.media.auth import authorize_http, principal, valid_stream_token
from vizora.media import stream
from tests.conftest import FAKE_USERS

_A = "cam/11111111-1111-1111-1111-111111111111"
_B = "cam/22222222-2222-2222-2222-222222222222"
_C = "cam/33333333-3333-3333-3333-333333333333"

CLIENTS: dict = {}


@pytest.fixture(autouse=True)
def _fake_clients(monkeypatch):
    CLIENTS.clear()

    async def _row(cid):
        c = CLIENTS.get(cid)
        return SimpleNamespace(enabled=c.enabled, api_key_hash=c.api_key_hash) if c else None

    monkeypatch.setattr(media_auth, "_client_row", _row)
    yield
    CLIENTS.clear()


def _client(enabled=True, key_hash="a" * 64):
    c = SimpleNamespace(id=uuid.uuid4(), enabled=enabled, api_key_hash=key_hash)
    CLIENTS[str(c.id)] = c
    return c


def _req(token):
    return SimpleNamespace(cookies={}, query_params={"token": token}, headers={})


async def test_client_token_valid_while_client_enabled():
    c = _client()
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    assert await valid_stream_token(tok, _A) is not None
    assert await authorize_http(_req(tok), _A) is not None


async def test_disabled_client_token_rejected():
    c = _client()
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    c.enabled = False
    assert await valid_stream_token(tok, _A) is None
    assert await authorize_http(_req(tok), _A) is None


async def test_rotated_key_kills_old_tokens():
    c = _client(key_hash="a" * 64)
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    c.api_key_hash = "b" * 64
    assert await valid_stream_token(tok, _A) is None


async def test_deleted_client_token_rejected():
    c = _client()
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    CLIENTS.clear()
    assert await valid_stream_token(tok, _A) is None


async def test_admin_token_dies_with_password_change():
    FAKE_USERS["boss"] = SimpleNamespace(role="admin", token_version=1)
    tok, _ = stream.make_stream_token(_A, stream.owner_user("boss", 1))
    assert await valid_stream_token(tok, _A) is not None
    media_auth._owner_cache.clear()
    FAKE_USERS["boss"].token_version = 2
    assert await valid_stream_token(tok, _A) is None


async def test_legacy_token_without_owner_still_works():
    """Deploy'dan oldin berilgan tokenlar (egasiz) muddati tugaguncha ishlaydi."""
    tok, _ = stream.make_stream_token(_A)
    assert await valid_stream_token(tok, _A) is not None


async def test_owner_lookup_is_cached(monkeypatch):
    calls = 0
    c = _client()

    async def _row(cid):
        nonlocal calls
        calls += 1
        return SimpleNamespace(enabled=True, api_key_hash=c.api_key_hash)

    monkeypatch.setattr(media_auth, "_client_row", _row)
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    for _ in range(20):
        assert await valid_stream_token(tok, _A) is not None
    assert calls == 1


def test_principal_is_owner_not_token():
    c = _client()
    t1, _ = stream.make_stream_token(_A, stream.owner_client(c))
    t2, _ = stream.make_stream_token(_B, stream.owner_client(c))
    p1 = principal(stream.stream_claims(t1), t1)
    p2 = principal(stream.stream_claims(t2), t2)
    assert p1 == p2 == f"c:{c.id}"


# --- WS endpoint: ega bo'yicha chegara va bekor qilinganda uzilish ---------------

class _Sub:
    dropped = 0

    async def get(self):
        await asyncio.sleep(3600)


class _Hub:
    def __init__(self):
        self.subs = set()

    def subscribe(self):
        s = _Sub()
        self.subs.add(s)
        return s

    def unsubscribe(self, s):
        self.subs.discard(s)


@pytest.fixture
def ws_app(monkeypatch):
    hubs: dict = {}
    monkeypatch.setattr(ws_mod.ws_stream, "get_hub", lambda p: hubs.setdefault(p, _Hub()))
    monkeypatch.setattr(ws_mod.settings, "ws_stream_enabled", True)
    ws_mod._open.clear()
    ws_mod._open_total.clear()
    app = FastAPI()
    app.include_router(ws_mod.router)
    yield TestClient(app)
    ws_mod._open.clear()
    ws_mod._open_total.clear()


def test_ws_total_cap_is_per_owner_not_per_token(ws_app, monkeypatch):
    monkeypatch.setattr(ws_mod, "_MAX_PER_CLIENT", 2)
    c = _client()
    toks = [stream.make_stream_token(p, stream.owner_client(c))[0] for p in (_A, _B, _C)]
    with ws_app.websocket_connect(f"/ws/stream/{_A}?token={toks[0]}"), \
            ws_app.websocket_connect(f"/ws/stream/{_B}?token={toks[1]}"):
        # Uchinchi kamera, YANGI token — baribir o'sha mijoz: rad.
        with pytest.raises(WebSocketDisconnect) as e:
            with ws_app.websocket_connect(f"/ws/stream/{_C}?token={toks[2]}") as w:
                w.receive_text()
        assert e.value.code == 4429
    # Boshqa mijoz o'z chegarasiga ega.
    other = _client()
    t, _ = stream.make_stream_token(_C, stream.owner_client(other))
    with ws_app.websocket_connect(f"/ws/stream/{_C}?token={t}"):
        pass
    assert ws_mod._open_total == {}


def test_open_ws_closed_when_client_disabled(ws_app, monkeypatch):
    monkeypatch.setattr(ws_mod, "OWNER_RECHECK_SECONDS", 0.05)
    monkeypatch.setattr(media_auth, "OWNER_CACHE_SECONDS", 0.0)
    c = _client()
    tok, _ = stream.make_stream_token(_A, stream.owner_client(c))
    with ws_app.websocket_connect(f"/ws/stream/{_A}?token={tok}") as w:
        c.enabled = False
        with pytest.raises(WebSocketDisconnect) as e:
            w.receive_text()
        assert e.value.code == 4401
