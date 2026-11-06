"""Kamera modeli. RTSP URL shifrlangan (BYTEA) saqlanadi."""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vizora.core.db import Base


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Ixtiyoriy hudud (filial ostida). NULL bo'lishi mumkin — kamera hech qaysi
    # hududga biriktirilmasligi mumkin (tekis ishlaydi). Tartiblash uchun.
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))

    # Manba turi: "rtsp" (oddiy IP kamera) | "ezviz" (EZVIZ bulut, RTSP'siz) |
    # "isup" (Hikvision ISUP 5.0 — qurilma o'zi serverga ulanadi, bulutsiz).
    # EZVIZ kameralarda RTSP yo'q — oqim bulutdan tortilib MediaMTX'ga publish qilinadi.
    source_type: Mapped[str] = mapped_column(String(10), default="rtsp", index=True)

    # To'liq RTSP URL (login/parol bilan) — AES-256-GCM bilan shifrlangan.
    # EZVIZ kameralar uchun bo'sh (NULL). ISUP kamerada backend o'zi to'ldiradi
    # (`rtsp://mediamtx:8554/isup/<id>/<kanal>`) — shuning uchun RTSP yo'lidagi
    # hamma narsa (transkod, probe, snapshot, WS) o'zgarishsiz ishlaydi.
    rtsp_url_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # --- ISUP (source_type="isup") maydonlari ---
    # Qurilmadagi "Device ID" (ISUP sozlamasida o'zimiz beramiz) va kanal (NVR'da 1..N).
    isup_device_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    isup_channel: Mapped[int] = mapped_column(Integer, default=1)

    # --- EZVIZ (source_type="ezviz") maydonlari ---
    # Kamera seriya raqami (EZVIZ bulutida identifikator).
    ezviz_serial: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Kanal (odatda 1).
    ezviz_channel: Mapped[int] = mapped_column(Integer, default=1)
    # EZVIZ kamera qaysi bulut hisobiga tegishli (login/parol shu yerdan olinadi).
    # RTSP kameralarda NULL.
    ezviz_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ezviz_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Shifrlangan (batareyali) kameralar uchun "Tasdiqlash Kodu" (verification code)
    # — AES-256-GCM bilan shifrlab saqlanadi. Bo'lsa video NAL dekod qilinadi.
    ezviz_verify_code_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # Buzuq kadrlarni tashlash: oqim juda yo'qotuvchi (weak uplink) bo'lsa, PyAV
    # kadrlarni dekodlab faqat TOZA (xatosiz) kadrlarni o'tkazadi (oxirgi toza
    # ushlab turiladi). Past-fps, ammo buzilishsiz. Faqat shu kameralarga yoqiladi.
    ezviz_drop_corrupt: Mapped[bool] = mapped_column(Boolean, default=False)

    # Bulut SUBSTREAM (stream=2, past o'lcham) ni ishlatish. Buziladigan yuqori-res
    # kameralar uchun: past o'lcham -> paket yo'qolmaydi -> toza; H.264 bo'lsa
    # transkodsiz copy (CPU ~0). Substream bo'lmasa publisher asosiy oqimga qaytadi.
    # PER-KAMERA: admin faqat kerakli kameralarga yoqadi (boshqalari HD'da qoladi).
    ezviz_use_substream: Mapped[bool] = mapped_column(Boolean, default=False)

    # DOIM ISSIQ: tomoshabin bo'lmasa ham oqim 24/7 olinib turadi — "Ko'rish" bir
    # zumda ochiladi. EZVIZ/Hik: publisher doim tirik (transkod ~1 yadro yoki NVENC);
    # RTSP: MediaMTX manbani on-demand emas, doimiy tortadi; ISUP: asosiy oqim doim
    # ochiq. Panelda kamera formasidan yoqiladi ([[manager.run_always_warm]]).
    keep_warm: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    # Sub-stream (past sifatli profil) RTSP URL — ABR uchun. Ixtiyoriy.
    # Mavjud bo'lsa, MediaMTX'da "cam/<id>/sub" path yaratiladi va master.m3u8
    # ikki variant (HD + SD) bilan beriladi -> hls.js tarmoqqa qarab avto-tanlaydi.
    rtsp_sub_url_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    codec: Mapped[str] = mapped_column(String(10), default="auto")  # h264|h265|auto
    transcode: Mapped[bool] = mapped_column(Boolean, default=False)

    # Sub-stream kodeki va transkod bayrog'i — ASOSIY oqimdan MUSTAQIL. Kameraning
    # ikkinchi profili deyarli doim H.264 (past o'lcham) bo'ladi, asosiysi esa HEVC
    # bo'lishi mumkin. Ilgari sub path asosiy oqimning `transcode` bayrog'ini meros
    # olardi -> HEVC kamerada substream ham bekorga transkod qilinib, narx IKKI
    # barobar bo'lardi. Endi sub o'z kodeki bo'yicha hal qilinadi: aniqlanmaguncha
    # ("auto") transkodsiz to'g'ridan tortiladi (CPU/GPU ~0), probe HEVC topsagina
    # yoqiladi.
    sub_codec: Mapped[str] = mapped_column(String(10), default="auto")  # h264|h265|auto
    sub_transcode: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[str] = mapped_column(
        String(10), default="offline", index=True
    )  # online|offline|error
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # MediaMTX path: "cam/<camera_id>"
    stream_path: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    zone: Mapped["Zone | None"] = relationship(back_populates="cameras")  # noqa: F821
    ezviz_account: Mapped["EzvizAccount | None"] = relationship()  # noqa: F821

    @property
    def has_sub(self) -> bool:
        """ABR uchun sub-stream mavjudmi (master.m3u8 shu asosda beriladi).

        RTSP kamerada apparat sub-stream (rtsp_sub_url) bo'lsa; EZVIZ kamerada esa
        publisher har doim ikkinchi (SD) rendition chiqaradi — shuning uchun ABR
        doim mavjud."""
        return self.rtsp_sub_url_enc is not None or self.is_ezviz

    @property
    def is_ezviz(self) -> bool:
        return self.source_type == "ezviz"

    @property
    def is_isup(self) -> bool:
        return self.source_type == "isup"

    @property
    def always_warm(self) -> bool:
        """Kamera doim issiq turadimi: panelda yoqilgan (`keep_warm`) YOKI eski
        `ALWAYS_WARM_CAMERA_IDS` ro'yxatida.

        Frontend buni bilishi kerak: bunday kameraning playlist'i doim to'la
        (10 segment), shuning uchun pleyer jonli chetga yaqinroq boshlashi mumkin
        — ortiqcha buferlash kutishi shart emas."""
        if self.keep_warm:
            return True
        from vizora.core.config import settings  # kech import — model qatlamini yengil tutamiz

        return str(self.id) in set(settings.always_warm_ids)

    @property
    def has_verify_code(self) -> bool:
        """Shifrlangan EZVIZ kamera uchun dekod kodi sozlanganmi."""
        return self.ezviz_verify_code_enc is not None
