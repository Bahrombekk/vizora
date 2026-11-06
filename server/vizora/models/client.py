"""Foydalanuvchilar (admin panel) va API mijozlari (tashqi tizimlar)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from vizora.core.db import Base


class User(Base):
    """Admin panelga kiradigan foydalanuvchi (parol bilan)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="admin")  # admin|operator
    # Sessiyalarni bekor qilish: JWT ichidagi "ver" shu bilan teng bo'lishi shart. Parol
    # almashganda oshiriladi — boshqa qurilmalardagi (yoki o'g'irlangan) tokenlar o'ladi.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiClient(Base):
    """Tashqi tizim — WS/REST'ga API key bilan kiradi, scope bilan cheklangan."""

    __tablename__ = "api_clients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    # Auth lookup kaliti — UNIQUE (bir hash bir mijoz; tasodifan ham takrorlanmasin)
    api_key_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # scopes: {"branches": [...], "zones": [...], "all": bool}
    scopes: Mapped[dict] = mapped_column(JSONB, default=dict)
    # O'chirib qo'yish/qayta yoqish (kalitni o'chirmasdan bloklash)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    # Oxirgi marta API kalit ishlatilgan vaqt (kuzatuv uchun)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
