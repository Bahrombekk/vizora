"""FastAPI dependency'lar — autentifikatsiya va rol tekshiruvi."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, update

from vizora.core.db import SessionLocal
from vizora.models.client import ApiClient, User
from vizora.core.security import decode_access_token, hash_api_key

# Admin JWT HttpOnly cookie nomi (localStorage o'rniga — XSS bilan o'g'irlanmasin).
ADMIN_COOKIE = "vz_admin"
# Header'dan ham qabul qilamiz (orqaga moslik / docs "Authorize"); auto_error=False
# — token cookie'da bo'lishi mumkin, shu sabab header majburiy emas.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

# last_used_at yangilashlar orasidagi eng kichik oraliq (read yo'lida write tejash).
_LAST_USED_THROTTLE_SECONDS = 60


class CurrentUser:
    def __init__(self, username: str, role: str, token_version: int = 0):
        self.username = username
        self.role = role
        self.token_version = token_version


def origin_allowed(headers) -> bool:
    """Brauzer so'rovi panelning O'Z sahifasidan kelganmi (CSRF / WS hijacking'ga qarshi).

    `Origin` yo'q — brauzer emas (curl, server-server) yoki same-origin GET: ruxsat.
    Bor bo'lsa — uning host'i Host / X-Forwarded-Host ga teng yoki ishonchli ro'yxatda."""
    from urllib.parse import urlsplit

    from vizora.core.config import settings

    origin = (headers.get("origin") or "").strip()
    if not origin or not settings.csrf_origin_check:
        return True
    if origin == "null":
        return False
    trusted = {o.strip().rstrip("/") for o in settings.cors_origins}
    trusted |= {o.strip().rstrip("/") for o in settings.trusted_origins.split(",") if o.strip()}
    if origin.rstrip("/") in trusted:
        return True
    netloc = urlsplit(origin).netloc.lower()
    if not netloc:
        return False
    hosts = {(headers.get("host") or "").lower()}
    hosts |= {h.strip().lower() for h in (headers.get("x-forwarded-host") or "").split(",") if h.strip()}
    hosts.discard("")
    if netloc in hosts:
        return True
    # Proksi Host'dan portni olib tashlagan bo'lsa (nginx `$host`), faqat host nomi bilan.
    origin_name = netloc.rsplit(":", 1)[0] if not netloc.endswith("]") else netloc
    return any(":" not in h and h == origin_name for h in hosts)


async def _user_row(username: str):
    """(role, token_version) — yoki None. Testlarda almashtiriladi."""
    async with SessionLocal() as db:
        return (await db.execute(
            select(User.role, User.token_version).where(User.username == username)
        )).first()


async def get_current_user(
    request: Request,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> CurrentUser:
    # Token manbai: Authorization: Bearer (header) YOKI vz_admin cookie.
    if not token:
        token = request.cookies.get(ADMIN_COOKIE)
    payload = decode_access_token(token) if token else None
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token yaroqsiz yoki muddati o'tgan",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Token imzosi to'g'ri bo'lsa ham foydalanuvchi BAZADA tekshiriladi: o'chirilgan
    # foydalanuvchi, almashgan parol (`token_version`) yoki o'zgargan rol darhol kuchga
    # kiradi — aks holda o'g'irlangan token 12 soatgacha ishlab turardi.
    row = await _user_row(payload.get("sub", ""))
    if row is None or int(payload.get("ver", 0) or 0) != int(row.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessiya bekor qilingan — qayta kiring",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(username=payload.get("sub", ""), role=row.role,
                       token_version=int(row.token_version or 0))


def require_role(*roles: str):
    """Berilgan rollardan biri talab qilinadigan dependency."""

    async def _dep(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if roles and user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Ruxsat yetarli emas"
            )
        return user

    return _dep


# --- API kalit autentifikatsiyasi (tashqi tizimlar uchun, JWT'siz) ---
def _extract_api_key(
    x_api_key: str | None, api_key: str | None, authorization: str | None
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if api_key:
        return api_key.strip()
    return None


async def get_api_client(
    x_api_key: Annotated[str | None, Header()] = None,
    api_key: Annotated[str | None, Query()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> ApiClient:
    """API kalitni (header/query) tekshirib, ApiClient qaytaradi.

    `last_used_at`ni yangilaydi (kuzatuv). Kalit yaroqsiz yoki mijoz
    o'chirilgan bo'lsa 401.
    """
    raw = _extract_api_key(x_api_key, api_key, authorization)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API kalit talab qilinadi (X-API-Key sarlavhasi)",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    h = hash_api_key(raw)
    async with SessionLocal() as db:
        client = (
            await db.execute(select(ApiClient).where(ApiClient.api_key_hash == h))
        ).scalar_one_or_none()
        if client is None or not client.enabled:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API kalit yaroqsiz yoki o'chirilgan",
            )
        # last_used_at'ni har so'rovda EMAS — eng ko'pi daqiqada bir marta yozamiz
        # (read yo'lida ortiqcha write bo'lmasin; serqatnov mijoz DB'ni urmaydi).
        now = datetime.now(timezone.utc)
        last = client.last_used_at
        if last is None or (now - last).total_seconds() > _LAST_USED_THROTTLE_SECONDS:
            await db.execute(
                update(ApiClient).where(ApiClient.id == client.id).values(last_used_at=now)
            )
            await db.commit()
    return client
