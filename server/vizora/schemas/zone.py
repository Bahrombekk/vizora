"""Hudud sxemalari — filialga tegishli ixtiyoriy guruh."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ZoneCreate(BaseModel):
    branch_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50)
    location: str | None = Field(default=None, max_length=300)


class ZoneUpdate(BaseModel):
    branch_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=300)


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    branch_id: uuid.UUID
    name: str
    code: str
    location: str | None
    created_at: datetime
    # ISUP: hudud kaliti biriktirilganmi (kalitning o'zi bu yerda YO'Q) va prefiks.
    isup_key_id: uuid.UUID | None = None
    isup_prefix: str | None = None


class ZoneStats(ZoneOut):
    """Ro'yxat uchun: hududdagi kameralar va onlayn kameralar soni."""
    cameras: int = 0
    online: int = 0
