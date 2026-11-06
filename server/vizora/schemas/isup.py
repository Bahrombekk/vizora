"""ISUP kalitlari, hududga biriktirish va qurilmalar reestri."""
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, computed_field

# Qurilma web-interfeysidagi "Encryption Key" — harf/raqam, 8-32 belgi.
IsupKeyValue = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{8,32}$")]
IsupDeviceIdStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
IsupPrefix = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{2,32}$")]


class ZoneRef(BaseModel):
    id: uuid.UUID
    name: str
    branch_name: str | None = None


KeyChoice = Annotated[str, Field(pattern=r"^(default|new|[0-9a-fA-F-]{36})$")]


class IsupKeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    key: IsupKeyValue | None = None       # berilmasa tasodifiy yaratiladi
    zone_id: uuid.UUID | None = None   # egasi: hudud ichidagi kalit; NULL = umumiy


class IsupKeyUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)


class IsupKeyRotate(BaseModel):
    key: IsupKeyValue | None = None


class IsupKeyOut(BaseModel):
    """Kalitning o'zi bu yerda YO'Q — faqat `/reveal` (admin) yoki yaratilganda."""

    id: uuid.UUID
    label: str
    created_at: datetime
    rotated_at: datetime | None
    zone_id: uuid.UUID | None = None    # egasi (NULL = umumiy kalit)
    zone_name: str | None = None
    zones: list[ZoneRef] = []       # qaysi hududlarda STANDART kalit
    devices: int = 0  # shu kalit bilan ulanadigan qurilmalar (standart yoki tanlangan)
    cameras: int = 0  # o'sha qurilmalardagi ISUP kameralar

    @computed_field  # "Band" = hudud standarti yoki qurilmaga berilgan; aks holda "Bo'sh"
    @property
    def in_use(self) -> bool:
        return bool(self.zones) or self.devices > 0


class IsupKeySecret(BaseModel):
    id: uuid.UUID
    key: str
    label: str | None = None


class ZoneKeysCreate(BaseModel):
    count: int = Field(default=1, ge=1, le=50)
    label: str | None = Field(default=None, max_length=180)
    make_default: bool = False   # birinchisi hudud standarti bo'lsinmi


class ZoneIsupUpdate(BaseModel):
    isup_key_id: uuid.UUID | None = None
    isup_prefix: IsupPrefix | None = None


class ZoneIsupOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str = ""
    branch_name: str | None
    isup_key_id: uuid.UUID | None
    isup_prefix: str | None
    devices: int = 0


class IsupDeviceCreate(BaseModel):
    device_id: IsupDeviceIdStr
    label: str | None = Field(default=None, max_length=200)
    zone_id: uuid.UUID | None = None
    own_key: bool = False             # True — hudud ichida shu qurilmaga yangi kalit
    key_id: uuid.UUID | None = None   # yoki mavjud kalit


class IsupDeviceUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    zone_id: uuid.UUID | None = None
    blocked: bool | None = None
    key: KeyChoice | None = None      # default | new | <kalit id>


class IsupDeviceOut(BaseModel):
    id: uuid.UUID
    device_id: str
    label: str | None
    zone_id: uuid.UUID | None
    zone_name: str | None
    blocked: bool
    has_own_key: bool
    key_id: uuid.UUID | None = None    # qurilmaga tanlangan kalit (NULL = hudud standarti)
    key_label: str | None = None       # amalda ishlatiladigan kalit nomi
    key_source: str          # device | assigned | zone | default | none | blocked
    source: str              # manual | camera | prefix | auto
    online: bool
    connected_with: str | None = None  # bridge qaysi kalit bilan kiritdi (device/prefix:…/default)
    ip: str | None = None
    model: str | None = None
    firmware: str | None = None
    cameras: int = 0
    last_online_at: datetime | None
    created_at: datetime


class IsupRejected(BaseModel):
    device_id: str
    reason: str      # unknown | blocked | key_mismatch | no-id
    count: int
    age: int         # soniya oldin
    registered: bool  # reestrda bormi


class IsupOverview(BaseModel):
    bridge_ok: bool
    default_key_allowed: bool
    server_host: str
    server_port: int
    devices: list[IsupDeviceOut]
    rejected: list[IsupRejected]


class IsupEffectiveKey(BaseModel):
    device_id: str
    key: str | None
    source: str
    key_id: uuid.UUID | None = None
    key_label: str | None = None
