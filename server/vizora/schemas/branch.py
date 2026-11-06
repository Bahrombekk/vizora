"""Filial sxemalari — sodda tashkiliy birlik (ixtiyoriy). EZVIZ login/parol
bu yerda EMAS — u alohida EzvizAccount'da."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50)


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    code: str | None = Field(default=None, max_length=50)


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    code: str
    created_at: datetime


class BranchStats(BranchOut):
    """Ro'yxat uchun: filialdagi hududlar, kameralar va onlayn kameralar soni."""
    zones: int = 0
    cameras: int = 0
    online: int = 0
