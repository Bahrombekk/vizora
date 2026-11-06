"""Hudud modeli — filialga tegishli (faqat guruhlash uchun)."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vizora.core.db import Base


class Zone(Base):
    __tablename__ = "zones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    branch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("branches.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # --- ISUP 5.0 ([[vizora.models.isup]]) ---
    # Hudud qurilmalari ulanadigan kalit. Bitta kalit bir nechta hududga berilishi
    # mumkin; hududga alohida kalit kerak bo'lsa — o'ziga yangi kalit yaratiladi.
    isup_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("isup_keys.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Panelda hali yo'q qurilma Device ID'si shu bilan boshlansa (masalan "SCH1-"),
    # hudud kaliti bilan ulanadi va shu hududga yoziladi. Noyob.
    isup_prefix: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    branch: Mapped["Branch"] = relationship(  # noqa: F821
        back_populates="zones"
    )
    # Hudud o'chirilsa kameralar O'CHMAYDI — faqat biriktirilmagan bo'lib qoladi
    # (DB FK: SET NULL). passive_deletes: ORM kameralarni o'zi o'chirmaydi.
    cameras: Mapped[list["Camera"]] = relationship(  # noqa: F821
        back_populates="zone",
        passive_deletes=True,
    )
