"""Panel orqali parol almashtirish — /api/auth/change-password.

`.env` dagi ADMIN_PASSWORD faqat birinchi ishga tushishda ishlaydi; keyin parol
shu endpoint orqali `users.password_hash` da o'zgaradi.
"""
from types import SimpleNamespace

import pytest

from tests.conftest import FAKE_USERS
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vizora.api.panel.auth import router as auth_router
from vizora.core.db import get_db
from vizora.api.deps import ADMIN_COOKIE
from vizora.core.security import hash_password, verify_password

_PW = "secret123"


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
    """Bitta userli soxta sessiya — commit oddiygina "yozildi" deb belgilaydi."""

    def __init__(self, user):
        self._user = user
        self.commits = 0

    async def execute(self, stmt):
        return _FakeResult(self._user)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def ctx():
    user = SimpleNamespace(
        username="admin", role="admin", password_hash=hash_password(_PW), token_version=0,
    )
    db = _FakeDB(user)
    FAKE_USERS["admin"] = user
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    app.state.redis = _FakeRedis()

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    client = TestClient(app)
    client.post("/api/auth/login", data={"username": "admin", "password": _PW})
    return SimpleNamespace(client=client, user=user, db=db)


def _change(client, cur, new):
    return client.post(
        "/api/auth/change-password",
        json={"current_password": cur, "new_password": new},
    )


def test_change_password_updates_hash(ctx):
    r = _change(ctx.client, _PW, "yangi-parol-123")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ctx.db.commits == 1
    assert verify_password("yangi-parol-123", ctx.user.password_hash)
    assert not verify_password(_PW, ctx.user.password_hash)


def test_change_password_refreshes_cookie(ctx):
    """Sessiya uzilmasin — javob yangi vz_admin cookie'sini o'rnatadi."""
    r = _change(ctx.client, _PW, "yangi-parol-123")
    sc = r.headers.get("set-cookie", "")
    assert ADMIN_COOKIE in sc and "httponly" in sc.lower()


def test_wrong_current_password_rejected(ctx):
    r = _change(ctx.client, "noto'g'ri", "yangi-parol-123")
    assert r.status_code == 400
    assert ctx.db.commits == 0
    assert verify_password(_PW, ctx.user.password_hash)  # eski parol saqlanib qoldi


def test_same_password_rejected(ctx):
    assert _change(ctx.client, _PW, _PW).status_code == 400
    assert ctx.db.commits == 0


def test_short_new_password_rejected(ctx):
    assert _change(ctx.client, _PW, "qisqa").status_code == 422  # min_length=8
    assert ctx.db.commits == 0


def test_anonymous_cannot_change_password(ctx):
    ctx.client.cookies.clear()
    assert _change(ctx.client, _PW, "yangi-parol-123").status_code == 401


def test_me_returns_session_owner(ctx):
    r = ctx.client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"username": "admin", "role": "admin"}
