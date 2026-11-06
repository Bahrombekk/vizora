"""config.Settings — fail-closed sir guardlari va production aniqlash."""
from vizora.core.config import Settings

_GOOD_JWT = "x" * 40
_GOOD_AES = "0123456789abcdef" * 4  # 64 hex, yetarli entropiya
_GOOD_PW = "Str0ngPassw0rd!"


def _s(**kw) -> Settings:
    base = dict(
        environment="production",
        jwt_secret=_GOOD_JWT,
        aes_key_hex=_GOOD_AES,
        admin_password=_GOOD_PW,
        mediamtx_password="m" * 24,
    )
    base.update(kw)
    return Settings(**base)


def test_good_secrets_pass():
    assert _s().insecure_defaults() == []


def test_weak_jwt_caught():
    assert "JWT_SECRET" in _s(jwt_secret="change-me-in-production").insecure_defaults()
    assert "JWT_SECRET" in _s(jwt_secret="secret").insecure_defaults()
    assert "JWT_SECRET" in _s(jwt_secret="short").insecure_defaults()  # < 32


def test_weak_aes_caught():
    assert "AES_KEY_HEX" in _s(aes_key_hex="0" * 64).insecure_defaults()       # all-zero
    assert "AES_KEY_HEX" in _s(aes_key_hex="1" * 64).insecure_defaults()       # all-same
    assert "AES_KEY_HEX" in _s(aes_key_hex="01" * 32).insecure_defaults()      # 2 belgi
    assert "AES_KEY_HEX" in _s(aes_key_hex="zz" * 32).insecure_defaults()      # hex emas
    assert "AES_KEY_HEX" in _s(aes_key_hex="abc").insecure_defaults()          # qisqa


def test_weak_admin_password_caught():
    assert "ADMIN_PASSWORD" in _s(admin_password="admin").insecure_defaults()
    assert "ADMIN_PASSWORD" in _s(admin_password="short").insecure_defaults()  # < 8


def test_non_development_is_production():
    # "development"dan boshqa har qanday qiymat -> production-darajada (fail-closed).
    assert Settings(environment="production").is_production
    assert Settings(environment="staging").is_production
    assert Settings(environment="prod-typo").is_production
    assert Settings(environment="").is_production
    assert not Settings(environment="development").is_production
    assert not Settings(environment="  Development ").is_production  # trim + lower


def test_mediamtx_password_required_in_production():
    """MediaMTX ichki paroli bo'sh/qisqa bo'lsa prod ishga tushmaydi (audit 2026-11-03)."""
    assert "MEDIAMTX_PASSWORD" in _s(mediamtx_password="").insecure_defaults()
    assert "MEDIAMTX_PASSWORD" in _s(mediamtx_password="short").insecure_defaults()
