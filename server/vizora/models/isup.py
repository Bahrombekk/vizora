"""ISUP 5.0 kalitlari va qurilmalari.

Qurilma serverga ulanganda (`ENUM_DEV_AUTH`) isup-bridge uning Device ID'si bo'yicha
BITTA kalit qaytarishi kerak — ikki variantni sinab bo'lmaydi (noto'g'ri kalit =
"KALIT XATO", qurilma esa kutib qayta urinadi). Shuning uchun kalit Device ID'dan
aniq topiladi, ustunlik tartibi ([[isup.key_lines]]):

  1. qurilma bloklangan          -> rad
  2. qurilmaning o'z kaliti (key_enc) yoki unga tanlangan kalit (key_id) -> shu
  3. qurilma hududining standart kaliti (Zone.isup_key_id) -> shu
  4. Device ID hudud prefiksi bilan boshlanadi (panelda hali yo'q qurilma)
                                 -> o'sha hudud kaliti, qurilma o'sha hududga yoziladi
  5. umumiy zaxira `ISUP_KEY` (pilot; `ISUP_ALLOW_DEFAULT_KEY=false` bilan o'chadi)
  6. aks holda                   -> rad ("noma'lum qurilma")

Kalit alohida obyekt: bitta kalit bir nechta hududga berilishi ham, hududning
o'ziniki bo'lishi ham mumkin. Bridge'ga ochiq matn kerak, shuning uchun kalit
qaytariladigan shifrda (AES-256-GCM) saqlanadi, hash'da emas.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vizora.core.db import Base


class IsupKey(Base):
    __tablename__ = "isup_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    label: Mapped[str] = mapped_column(String(200))
    key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # Egasi: hudud ICHIDAGI kalit (hududda xohlagancha bo'lishi mumkin — standarti
    # `Zone.isup_key_id`, qolganlari alohida qurilmalarga). NULL = umumiy kalit
    # (bir nechta hududga standart sifatida beriladi). Hudud o'chsa uning kalitlari ham.
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("zones.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IsupDevice(Base):
    """ISUP qurilmasi (kamera yoki NVR). Bitta NVR ko'p kanal = ko'p kamera, shuning
    uchun kalit kameraga emas, qurilmaga bog'lanadi (`Camera.isup_device_id`)."""

    __tablename__ = "isup_devices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Qurilmaning O'Z kaliti — bo'lsa hudud kalitidan ustun (masalan bitta qurilma
    # kaliti sizib chiqqanda faqat shuni almashtirish uchun).
    key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Qurilma qaysi kalitda (odatda o'z hududining kalitlaridan biri). NULL = hudud
    # standarti. `key_enc` (eski "o'z kaliti") bundan ham ustun.
    key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("isup_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Qanday paydo bo'lgan: "manual" (panel), "camera" (kamera qo'shilganda), "prefix"
    # (o'zi ulandi, hudud prefiksi bo'yicha).
    source: Mapped[str] = mapped_column(String(10), default="manual")
    last_online_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    zone: Mapped["Zone | None"] = relationship()  # noqa: F821

    @property
    def has_own_key(self) -> bool:
        return self.key_enc is not None
