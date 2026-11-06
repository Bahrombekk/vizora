"""Filial modeli — kompaniyaning eng yuqori tashkiliy birligi (ixtiyoriy)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vizora.core.db import Base


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    zones: Mapped[list["Zone"]] = relationship(  # noqa: F821
        back_populates="branch",
        cascade="all, delete-orphan",
    )
