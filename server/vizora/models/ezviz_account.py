"""EZVIZ bulut hisobi — faqat login/parol "kalitlar qutisi".

Filial/tashkilot tushunchasidan voz kechilgan: EZVIZ kamera oqimini olish uchun
kerakli yagona narsa — EZVIZ akkount login/paroli. Bir akkountda bir nechta kamera
bo'lishi mumkin, shuning uchun parolni har kameraga takrorlamay shu yerda bir marta
saqlaymiz. RTSP kameralar bu modelga umuman bog'liq emas (ular mustaqil).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from vizora.core.db import Base


class EzvizAccount(Base):
    __tablename__ = "ezviz_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Admin uchun ko'rinadigan nom (masalan "Nurafshon EZVIZ").
    label: Mapped[str] = mapped_column(String(200))
    # Bulut turi: "ezviz" (apiisgp.ezvizlife.com, login v5/clientType1) yoki
    # "hik" (Hik-Connect: api.hik-connect.com, login v2/clientType55). Login oqimi
    # va VTM/pagelist clientType shunga qarab tanlanadi.
    provider: Mapped[str] = mapped_column(String(10), default="ezviz", server_default="ezviz")
    # EZVIZ login (email/telefon). Parol AES-256-GCM bilan shifrlanadi.
    account: Mapped[str] = mapped_column(String(200))
    password_enc: Mapped[bytes] = mapped_column(LargeBinary)
    # API region domeni (bo'sh = standart).
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
