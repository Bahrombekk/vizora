import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


class MeOut(BaseModel):
    """Joriy sessiya egasi — UI'da ism/rol ko'rsatish uchun."""

    username: str
    role: str


class PasswordChange(BaseModel):
    """Parol almashtirish — joriy parol tasdiq uchun majburiy.

    Yangi parol uzunligi `Settings.insecure_defaults()` bilan bir xil chegarada
    (kamida 8 belgi) — panel orqali env talabidan bo'sh parol qo'yilmasin.
    """

    current_password: str = Field(min_length=1)
    # bcrypt 72 baytdan keyingisini jimgina kesib tashlaydi — uzunroq parol "kuchli"
    # ko'rinardi, aslida esa faqat birinchi 72 bayti tekshirilardi.
    new_password: str = Field(min_length=8, max_length=72)


class ClientScopes(BaseModel):
    """API mijoz ruxsat doirasi — QAT'IY shakl (noto'g'ri tur saqlanmasin).

    `all` faqat bool; ro'yxatlar faqat UUID. Noma'lum kalit rad etiladi
    (`extra="forbid"`) — typo'li kalit jimgina "hech narsa" bo'lib qolmasin."""

    model_config = ConfigDict(extra="forbid")

    all: bool = False
    branches: list[uuid.UUID] = Field(default_factory=list)
    zones: list[uuid.UUID] = Field(default_factory=list)
    cameras: list[uuid.UUID] = Field(default_factory=list)


# --- API mijozlar (tashqi tizim) ---
class ApiClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: ClientScopes = Field(default_factory=ClientScopes)


class ApiClientUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    scopes: ClientScopes | None = None


class ApiClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    scopes: dict
    enabled: bool = True
    last_used_at: datetime | None = None
    created_at: datetime


class ApiClientStats(ApiClientOut):
    """Ro'yxat uchun: scope bo'yicha mijoz HAQIQATAN ko'ra oladigan kameralar soni
    (`access.can_access` bilan hisoblanadi — API bergan ruxsat bilan bir xil)."""
    cameras_allowed: int = 0
    online_allowed: int = 0


class ApiClientCreated(ApiClientOut):
    """Yaratilganda — xom api_key BIR MARTA qaytariladi."""

    api_key: str
