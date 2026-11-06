"""Umumiy test yordamchilari.

`get_current_user` endi har so'rovda foydalanuvchini BAZADAN tekshiradi (rol va
`token_version` — sessiyani bekor qilish). Testlar Postgres'siz ishlaydi, shuning
uchun bu so'rov xotiradagi ro'yxat bilan almashtiriladi.
"""
from types import SimpleNamespace

import pytest

# username -> obyekt (role, token_version). Test o'z foydalanuvchisini shu yerga qo'yadi.
FAKE_USERS: dict = {}
_DEFAULT_ROLES = {"admin": "admin", "op": "operator"}


@pytest.fixture(autouse=True)
def _fake_user_rows(monkeypatch):
    from vizora.api import deps

    FAKE_USERS.clear()

    async def _row(username):
        u = FAKE_USERS.get(username)
        if u is not None:
            return SimpleNamespace(role=u.role, token_version=getattr(u, "token_version", 0))
        if username in _DEFAULT_ROLES:
            return SimpleNamespace(role=_DEFAULT_ROLES[username], token_version=0)
        return None

    monkeypatch.setattr(deps, "_user_row", _row)
    yield
    FAKE_USERS.clear()


@pytest.fixture(autouse=True)
def _clear_stream_owner_cache():
    """Oqim token egasi keshi testlar orasida o'tib ketmasin."""
    from vizora.api.media import auth as media_auth

    media_auth._owner_cache.clear()
    yield
    media_auth._owner_cache.clear()
