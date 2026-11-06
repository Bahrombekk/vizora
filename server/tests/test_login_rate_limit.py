"""Login himoyasi (audit 2026-11-03):

* limit ATOMAR — parallel so'rovlar hammasi tekshiruvdan o'tib ketmasin;
* bcrypt event loop'ni to'xtatmaydi (thread'da);
* begona odam `admin` nomini bloklasa ham, tanish qurilmadan to'g'ri parol bilan
  kirish mumkin (device cookie).
"""
import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from vizora.api.panel import auth as auth_mod
from vizora.api.panel.auth import DEVICE_COOKIE, router as auth_router
from vizora.core import security
from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.core.security import hash_password
from tests.conftest import FAKE_USERS
from tests.test_auth_cookie import _FakeDB, _FakeRedis

_PW = "secret123"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_count", 1)
    monkeypatch.setattr(settings, "login_max_attempts", 10)
    user = SimpleNamespace(username="admin", role="admin",
                           password_hash=hash_password(_PW), token_version=0)
    FAKE_USERS["admin"] = user
    a = FastAPI()
    a.include_router(auth_router, prefix="/api")
    a.state.redis = _FakeRedis()

    async def _get_db():
        yield _FakeDB(user)

    a.dependency_overrides[get_db] = _get_db
    return a


def _client(app, ip):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://testserver",
                             headers={"X-Forwarded-For": ip})


async def _login(c, password):
    return await c.post("/api/auth/login", data={"username": "admin", "password": password})


async def test_parallel_wrong_logins_cannot_exceed_limit(app, monkeypatch):
    """30 ta bir vaqtdagi noto'g'ri urinishdan ko'pi bilan 10 tasi parolni sinaydi."""
    checked = 0
    real = security.averify_password

    async def _counting(plain, hashed):
        nonlocal checked
        checked += 1
        return await real(plain, hashed)

    monkeypatch.setattr(auth_mod, "averify_password", _counting)
    async with _client(app, "9.9.9.9") as c:
        rs = await asyncio.gather(*[_login(c, f"bad{i}") for i in range(30)])
    codes = [r.status_code for r in rs]
    assert codes.count(401) == 10
    assert codes.count(429) == 20
    assert checked == 10


async def test_bcrypt_does_not_block_event_loop(monkeypatch):
    monkeypatch.setattr(security, "verify_password", lambda p, h: time.sleep(0.3) or True)
    ticks = 0

    async def _ticker():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.02)
            ticks += 1

    await asyncio.gather(security.averify_password("x", "y"), _ticker())
    assert ticks == 10


async def test_attacker_cannot_lock_out_admin_on_known_device(app):
    async with _client(app, "2.2.2.2") as victim:
        assert (await _login(victim, _PW)).status_code == 200
        assert victim.cookies.get(DEVICE_COOKIE)

        # Hujumchi turli IP'lardan `admin` nomiga noto'g'ri parollar yuboradi.
        for i in range(12):
            async with _client(app, f"1.1.1.{i}") as atk:
                await _login(atk, "wrong")

        # Yangi qurilmadan — foydalanuvchi limiti ishlaydi (brute-force himoyasi).
        async with _client(app, "3.3.3.3") as fresh:
            assert (await _login(fresh, _PW)).status_code == 429

        # Tanish qurilmadan — to'g'ri parol bilan kiradi.
        assert (await _login(victim, _PW)).status_code == 200


async def test_known_device_still_limited_by_ip(app):
    async with _client(app, "4.4.4.4") as c:
        assert (await _login(c, _PW)).status_code == 200
        codes = [(await _login(c, "wrong")).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


async def test_successful_logins_do_not_accumulate(app):
    """Bitta NAT ortidagi ofis tez-tez kirsa o'zini bloklamasin."""
    async with _client(app, "5.5.5.5") as c:
        for _ in range(15):
            assert (await _login(c, _PW)).status_code == 200
