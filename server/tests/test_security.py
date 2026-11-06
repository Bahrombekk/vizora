"""Parol hash, JWT, API key."""
from vizora.core import security


def test_password_hash_verify():
    h = security.hash_password("secret123")
    assert h != "secret123"
    assert security.verify_password("secret123", h)
    assert not security.verify_password("wrong", h)


def test_api_key_hash_is_deterministic():
    """SHA-256 — bir xil kalit bir xil hash (WS lookup uchun)."""
    key = security.generate_api_key()
    assert key.startswith("vz_")
    assert security.hash_api_key(key) == security.hash_api_key(key)
    assert security.verify_api_key(key, security.hash_api_key(key))
    assert not security.verify_api_key("vz_boshqa", security.hash_api_key(key))


def test_jwt_roundtrip():
    token = security.create_access_token(subject="admin", role="admin")
    payload = security.decode_access_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"


def test_jwt_invalid():
    assert security.decode_access_token("bad.token.here") is None
