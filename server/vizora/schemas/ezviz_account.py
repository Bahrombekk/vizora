"""EZVIZ hisob (login/parol qutisi) sxemalari. Parol hech qachon qaytarilmaydi."""
import uuid
from datetime import datetime
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Provider = Literal["ezviz", "hik"]

# XAVFSIZLIK: login so'rovi `https://{region}/v3/users/login/...` ga ketadi va unda saqlangan
# parolning MD5 i bor (bulut uni parol o'rnida qabul qiladi). Region erkin bo'lsa, hisobni
# tahrirlay oladigan odam uni o'z serveriga qaratib parolni o'g'irlardi (va server ichki
# manzillarga so'rov yuborardi). Faqat EZVIZ / Hik-Connect bulut domenlari.
_REGION_RE = re.compile(
    r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.(ezvizlife\.com|ezviz\.com|hik-connect\.com|hik-connectru\.com|hikvision\.com)\Z"
)


def _check_region(v: str | None) -> str | None:
    if not v:
        return v
    v = v.strip().lower()
    if not _REGION_RE.match(v):
        raise ValueError("Region faqat EZVIZ / Hik-Connect bulut domeni bo'lishi mumkin (masalan apiisgp.ezvizlife.com)")
    return v


Region = Annotated[str, AfterValidator(_check_region)]


class EzvizAccountCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    account: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1)
    provider: Provider = "ezviz"
    region: Region | None = Field(default=None, max_length=100)


class EzvizAccountUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    account: str | None = Field(default=None, max_length=200)
    # Bo'sh/berilmasa -> parol o'zgarmaydi.
    password: str | None = None
    provider: Provider | None = None
    region: Region | None = Field(default=None, max_length=100)


class EzvizAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    account: str
    provider: str
    region: str | None
    created_at: datetime


class EzvizAccountStats(EzvizAccountOut):
    """Ro'yxat uchun: hisobdagi kameralar holati va bulut sessiyasi.

    `session_at` — saqlangan sessiya token fayli oxirgi yangilangan vaqt (UNIX s);
    None = hali login qilinmagan (birinchi oqim/kashfiyotda qilinadi)."""
    cameras: int = 0
    online: int = 0
    no_stream: int = 0
    session_at: float | None = None
