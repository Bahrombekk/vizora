"""Autentifikatsiya — admin login (JWT) va API mijoz boshqaruvi."""
import hashlib
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.core.db import get_db
from vizora.api.patch import apply_patch
from vizora.api.deps import ADMIN_COOKIE, CurrentUser, get_current_user, require_role
from vizora.models.camera import Camera
from vizora.models.client import ApiClient, User
from vizora.models.zone import Zone
from vizora.schemas.auth import (
    ApiClientCreate,
    ApiClientCreated,
    ApiClientOut,
    ApiClientStats,
    ApiClientUpdate,
    MeOut,
    PasswordChange,
    TokenResponse,
)
from vizora.services.access import can_access
from vizora.core.security import (
    aburn_password_check,
    ahash_password,
    averify_password,
    create_access_token,
    generate_api_key,
    hash_api_key,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_ip(request: Request) -> str:
    """Haqiqiy mijoz IP'si — X-Forwarded-For'ga FAQAT ishonchli proksi ortida
    bo'lganda ishonadi.

    nginx `$proxy_add_x_forwarded_for` mijoz yuborgan XFF'ga o'zi ko'rgan IP'ni
    QO'SHADI (oxiriga). Demak hujumchi yuborgan soxta qiymatlar chapda qoladi;
    haqiqiy IP — oxiridan `trusted_proxy_count`-inchisi. Proksi yo'q bo'lsa
    (count=0) XFF'ni butunlay e'tiborsiz qoldiramiz va TCP peer'dan olamiz.
    """
    n = settings.trusted_proxy_count
    if n > 0:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if parts:
                return parts[max(0, len(parts) - n)]
    return request.client.host if request.client else "?"


def _rl_keys(request: Request, username: str) -> tuple[str, str]:
    return f"login_attempts:ip:{_client_ip(request)}", f"login_attempts:user:{_uname(username)}"


# "Tanish qurilma" cookie'si: shu brauzerdan avval MUVAFFAQIYATLI kirilgan. Hujumchi
# `admin` nomiga har 5 daqiqada 10 ta noto'g'ri parol yuborib, haqiqiy adminni
# to'g'ri parol bilan ham 429 da ushlab tura olardi (audit 2026-11-03). Tanish
# qurilmaga foydalanuvchi-nomi limiti qo'llanmaydi (IP limiti qoladi) — hujumchi bu
# cookie'ni faqat to'g'ri parol bilan ola oladi. OWASP "device cookie" usuli.
DEVICE_COOKIE = "vz_dev"
_DEVICE_TTL = 90 * 24 * 3600


def _device_key(token: str) -> str:
    return "login_dev:" + hashlib.sha256(token.encode()).hexdigest()


def _uname(username: str) -> str:
    return (username or "?").strip().lower()[:128]


async def _trusted_device(request: Request, username: str) -> bool:
    tok = request.cookies.get(DEVICE_COOKIE)
    if not tok or len(tok) > 128:
        return False
    owner = await request.app.state.redis.get(_device_key(tok))
    if isinstance(owner, bytes):
        owner = owner.decode(errors="ignore")
    return owner == _uname(username)


async def _remember_device(request: Request, response: Response, username: str) -> None:
    tok = request.cookies.get(DEVICE_COOKIE)
    if not tok or not await _trusted_device(request, username):
        tok = secrets.token_urlsafe(32)
    redis = request.app.state.redis
    await redis.set(_device_key(tok), _uname(username), ex=_DEVICE_TTL)
    response.set_cookie(
        DEVICE_COOKIE, tok, max_age=_DEVICE_TTL, httponly=True, samesite="strict",
        secure=settings.is_production, path="/api/auth",
    )


async def _begin_attempt(request: Request, username: str) -> None:
    """Urinishni AVVAL sanaydi, keyin limitni tekshiradi (Redis INCR — atomar).

    Ilgari avval `GET` bilan o'qilib, hisob bcrypt'dan KEYIN oshirilardi: 500 ta
    parallel so'rov hammasi limitdan o'tib ketardi (oyna boshiga 10 emas, 500 ta
    parol sinovi). Muvaffaqiyatli kirish hisobni qaytaradi ([[_attempt_succeeded]]),
    shuning uchun amalda faqat MUVAFFAQIYATSIZ urinishlar to'planadi.
    Ikkala o'lcham — IP'ni almashtira oladigan hujumchi ham bitta hisobni cheksiz
    brute-force qila olmasin; tanish qurilmaga faqat IP limiti ([[_trusted_device]]).
    """
    redis = request.app.state.redis
    ip_key, user_key = _rl_keys(request, username)
    keys = [ip_key] if await _trusted_device(request, username) else [ip_key, user_key]
    over = False
    for k in keys:
        n = await redis.incr(k)
        if n == 1:
            await redis.expire(k, settings.login_window_seconds)
        over = over or n > settings.login_max_attempts
    if over:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.",
        )


async def _attempt_succeeded(request: Request, username: str) -> None:
    """To'g'ri parol: IP hisobidan shu urinishni qaytaramiz (bitta NAT ortidagi ofis
    tez-tez kirsa o'zini bloklamasin), foydalanuvchi hisobini tozalaymiz."""
    redis = request.app.state.redis
    ip_key, user_key = _rl_keys(request, username)
    if int(await redis.decr(ip_key)) <= 0:
        await redis.delete(ip_key)
    await redis.delete(user_key)


def _set_admin_cookie(response: Response, token: str) -> None:
    """JWT'ni HttpOnly cookie'ga yozadi — JS o'qiy olmaydi (XSS himoyasi).

    SameSite=Lax cross-site mutatsiyalarda cookie yubormaydi (CSRF himoyasi).
    Secure faqat production'da (dev http://localhost'da cookie ishlashi uchun).
    """
    response.set_cookie(
        ADMIN_COOKIE, token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True, samesite="lax", secure=settings.is_production, path="/",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    response: Response,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    await _begin_attempt(request, form.username)
    user = (
        await db.execute(select(User).where(User.username == form.username))
    ).scalar_one_or_none()
    if user is None:
        await aburn_password_check(form.password)  # vaqt bo'yicha login mavjudligi bilinmasin
    if user is None or not await averify_password(form.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login yoki parol noto'g'ri",
        )
    await _attempt_succeeded(request, form.username)
    await _remember_device(request, response, user.username)
    token = create_access_token(subject=user.username, role=user.role, ver=user.token_version)
    _set_admin_cookie(response, token)
    # Token body'da ham qaytadi (orqaga moslik); frontend uni saqlamaydi.
    return TokenResponse(access_token=token, role=user.role)


@router.post("/logout")
async def logout(response: Response) -> dict:
    """Admin cookie'sini tozalaydi (auth talab qilinmaydi — har doim chiqish mumkin)."""
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=MeOut)
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> MeOut:
    """Joriy sessiya egasi (cookie'dagi JWT'dan) — panel sarlavhasi uchun."""
    return MeOut(username=user.username, role=user.role)


@router.post("/change-password")
async def change_password(
    request: Request,
    response: Response,
    payload: PasswordChange,
    db: Annotated[AsyncSession, Depends(get_db)],
    current: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    """Foydalanuvchi O'Z parolini almashtiradi (joriy parol bilan tasdiqlab).

    `.env` dagi ADMIN_PASSWORD faqat birinchi ishga tushishda (DB bo'sh bo'lganda)
    ishlaydi — keyin parol shu yerda, `users.password_hash` da o'zgaradi.
    """
    # Joriy parolni topishga urinish ham login kabi cheklanadi (brute-force'ga qarshi).
    await _begin_attempt(request, current.username)
    user = (
        await db.execute(select(User).where(User.username == current.username))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Foydalanuvchi topilmadi",
        )
    if not await averify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Joriy parol noto'g'ri"
        )
    await _attempt_succeeded(request, current.username)
    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Yangi parol eskisidan farq qilishi kerak",
        )
    user.password_hash = await ahash_password(payload.new_password)
    # Boshqa qurilmalardagi (yoki o'g'irlangan) sessiyalar shu zahoti bekor bo'ladi.
    user.token_version = int(user.token_version or 0) + 1
    await db.commit()
    # Shu qurilmadagi sessiya uzilmasin: cookie yangi versiyali token bilan almashtiriladi.
    _set_admin_cookie(response, create_access_token(subject=user.username, role=user.role,
                                                    ver=user.token_version))
    return {"ok": True}


# --- API mijozlar (tashqi tizimlar uchun key) ---
@router.post(
    "/clients", response_model=ApiClientCreated, status_code=status.HTTP_201_CREATED
)
async def create_client(
    payload: ApiClientCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> ApiClientCreated:
    raw_key = generate_api_key()
    client = ApiClient(
        name=payload.name,
        api_key_hash=hash_api_key(raw_key),
        # mode="json": UUID'lar string'ga, bool bool bo'lib JSONB'ga yoziladi.
        scopes=payload.scopes.model_dump(mode="json"),
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return ApiClientCreated(
        id=client.id,
        name=client.name,
        scopes=client.scopes,
        created_at=client.created_at,
        api_key=raw_key,  # faqat shu yerda — keyin ko'rsatilmaydi
    )


@router.get("/clients", response_model=list[ApiClientStats])
async def list_clients(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> list[dict]:
    """Mijozlar + har biri scope bo'yicha ko'ra oladigan kameralar soni (jami/onlayn)."""
    clients = (await db.execute(select(ApiClient).order_by(ApiClient.created_at.desc()))).scalars().all()
    cams = (await db.execute(
        select(Camera, Zone).outerjoin(Zone, Camera.zone_id == Zone.id))).all()
    out = []
    for c in clients:
        n = on = 0
        for cam, fac in cams:
            if can_access(c, cam, fac):
                n += 1
                on += cam.status == "online"
        out.append({**ApiClientOut.model_validate(c).model_dump(),
                    "cameras_allowed": n, "online_allowed": on})
    return out


@router.patch("/clients/{cid}", response_model=ApiClientOut)
async def update_client(
    cid: uuid.UUID,
    payload: ApiClientUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> ApiClient:
    client = await db.get(ApiClient, cid)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API mijoz topilmadi")
    # mode="json": scopes ichidagi UUID'lar JSONB uchun string'ga aylantiriladi.
    apply_patch(client, payload.model_dump(mode="json", exclude_unset=True))
    await db.commit()
    await db.refresh(client)
    return client


@router.post("/clients/{cid}/rotate-key", response_model=ApiClientCreated)
async def rotate_client_key(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> ApiClientCreated:
    """Yangi API kalit generatsiya qiladi (eski kalit DARHOL bekor bo'ladi).

    Kalit hash holda saqlanadi — eskisini qayta ko'rsatib bo'lmaydi; yo'qotilsa
    shu orqali yangilanadi. Yangi xom kalit FAQAT shu javobda qaytariladi.
    """
    client = await db.get(ApiClient, cid)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API mijoz topilmadi")
    raw_key = generate_api_key()
    client.api_key_hash = hash_api_key(raw_key)
    await db.commit()
    await db.refresh(client)
    return ApiClientCreated(
        id=client.id,
        name=client.name,
        scopes=client.scopes,
        created_at=client.created_at,
        api_key=raw_key,
    )


@router.delete("/clients/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    cid: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[CurrentUser, Depends(require_role("admin"))],
) -> None:
    client = await db.get(ApiClient, cid)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API mijoz topilmadi")
    await db.delete(client)
    await db.commit()
