"""Parol hash (bcrypt), JWT yaratish/tekshirish, API key generatsiya."""
import asyncio
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from vizora.core.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# --- Parollar ---
def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


# Foydalanuvchi topilmaganda ham bcrypt bajariladi — javob vaqti bo'yicha qaysi login
# mavjudligini bilib bo'lmasin (aks holda ~200 ms farq qiladi).
_DUMMY_HASH = _pwd.hash("timing-equaliser-not-a-password")


def burn_password_check(plain: str) -> None:
    _pwd.verify(plain, _DUMMY_HASH)


# bcrypt ~200-300 ms CPU — `async` handler ichida to'g'ridan-to'g'ri chaqirilsa event
# loop'ni to'xtatadi: 500 ta parallel noto'g'ri login butun serverni (HLS, WS, SSE)
# ~2 daqiqaga muzlatardi (audit 2026-11-03). Endi thread'da, va bir vaqtda ko'pi bilan
# _BCRYPT_SLOTS ta — hujum CPU'ni ham to'liq egallab ololmasin.
_BCRYPT_SLOTS = 4
_bcrypt_sem: "asyncio.Semaphore | None" = None


async def _in_bcrypt_thread(fn, *args):
    global _bcrypt_sem
    if _bcrypt_sem is None:
        _bcrypt_sem = asyncio.Semaphore(_BCRYPT_SLOTS)
    async with _bcrypt_sem:
        return await asyncio.to_thread(fn, *args)


async def averify_password(plain: str, hashed: str) -> bool:
    return await _in_bcrypt_thread(verify_password, plain, hashed)


async def ahash_password(password: str) -> str:
    return await _in_bcrypt_thread(hash_password, password)


async def aburn_password_check(plain: str) -> None:
    await _in_bcrypt_thread(burn_password_check, plain)


# --- JWT (admin panel) ---
# Admin tokeni `typ: "access"` bilan; stream tokeni (`typ: "stream"`, [[stream]]) xuddi shu
# JWT_SECRET bilan imzolanadi, shuning uchun turi tekshirilmasa biri ikkinchisi o'rnida
# o'tib ketishi mumkin edi. `ver` — [[models.client.User.token_version]] (sessiya bekor qilish).
def create_access_token(subject: str, role: str, ver: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "typ": "access",
        "ver": int(ver or 0),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    # Eski tokenlarda `typ` yo'q (moslik), lekin boshqa tur (stream) — admin emas.
    if payload.get("typ", "access") != "access":
        return None
    return payload


# --- API key (tashqi tizimlar) ---
# Yuqori entropiyali tasodifiy token bo'lgani uchun bcrypt emas, SHA-256
# ishlatamiz — bu O(1) qidiruvga (api_key_hash bo'yicha indeks) imkon beradi.
def generate_api_key() -> str:
    """Bir marta ko'rsatiladigan xom kalit (faqat hash saqlanadi)."""
    return "vz_" + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_api_key(raw: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw), hashed)
