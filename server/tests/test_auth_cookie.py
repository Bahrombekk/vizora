"""Admin auth — JWT HttpOnly cookie'da (localStorage emas), header fallback bilan."""
from types import SimpleNamespace

import pytest

from tests.conftest import FAKE_USERS
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from vizora.api.panel.auth import router as auth_router
from vizora.core.db import get_db
from vizora.api.deps import ADMIN_COOKIE, CurrentUser, get_current_user, require_role
from vizora.core.security import create_access_token, hash_password


# ---------- get_current_user: cookie YOKI header ----------
def _req(cookies=None):
    return SimpleNamespace(cookies=cookies or {})


async def test_cookie_token_accepted():
    tok = create_access_token(subject="admin", role="admin")
    user = await get_current_user(_req({ADMIN_COOKIE: tok}), token=None)
    assert user.username == "admin" and user.role == "admin"


async def test_header_token_still_accepted():  # orqaga moslik
    tok = create_access_token(subject="op", role="operator")
    user = await get_current_user(_req(), token=tok)
    assert user.role == "operator"


async def test_no_token_rejected():
    with pytest.raises(HTTPException) as ei:
        await get_current_user(_req(), token=None)
    assert ei.value.status_code == 401


async def test_garbage_cookie_rejected():
    with pytest.raises(HTTPException):
        await get_current_user(_req({ADMIN_COOKIE: "not-a-jwt"}), token=None)


# ---------- login/logout cookie oqimi (in-memory, Postgres'siz) ----------
class _FakeRedis:
    def __init__(self):
        self.store = {}

    async def incr(self, k):
        self.store[k] = self.store.get(k, 0) + 1
        return self.store[k]

    async def get(self, k):
        return self.store.get(k)

    async def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)

    async def expire(self, k, s):
        return True

    async def decr(self, k):
        self.store[k] = self.store.get(k, 0) - 1
        return self.store[k]

    async def set(self, k, v, ex=None):
        self.store[k] = v
        return True


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class _FakeDB:
    def __init__(self, user):
        self._user = user

    async def execute(self, stmt):
        return _FakeResult(self._user)


@pytest.fixture
def client():
    user = SimpleNamespace(
        username="admin", role="admin", password_hash=hash_password("secret123"), token_version=0,
    )
    FAKE_USERS["admin"] = user
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.state.redis = _FakeRedis()

    async def _get_db():
        yield _FakeDB(user)

    app.dependency_overrides[get_db] = _get_db

    @app.get("/api/_protected")
    async def _protected(u: CurrentUser = Depends(require_role("admin"))):  # noqa: B008
        return {"role": u.role}

    return TestClient(app)


def test_login_sets_httponly_cookie(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "secret123"})
    assert r.status_code == 200
    sc = r.headers.get("set-cookie", "")
    assert ADMIN_COOKIE in sc and "httponly" in sc.lower() and "samesite=lax" in sc.lower()
    assert ADMIN_COOKIE in client.cookies


def test_protected_route_accepts_cookie(client):
    client.post("/api/auth/login", data={"username": "admin", "password": "secret123"})
    r = client.get("/api/_protected")  # cookie jar'dan avtomatik yuboriladi
    assert r.status_code == 200 and r.json()["role"] == "admin"


def test_wrong_password_no_cookie(client):
    r = client.post("/api/auth/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert ADMIN_COOKIE not in client.cookies


def test_logout_clears_cookie(client):
    client.post("/api/auth/login", data={"username": "admin", "password": "secret123"})
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    # delete_cookie => max-age=0 / expires o'tgan
    sc = r.headers.get("set-cookie", "").lower()
    assert ADMIN_COOKIE in sc and ("max-age=0" in sc or "expires=" in sc)
