import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

# XAVFSIZLIK: RTSP URL MediaMTX'ning `runOnDemand` ffmpeg buyrug'iga `-i "<url>"` bo'lib
# tushadi (transkod) va ffprobe/ffmpeg'ga beriladi. Qo'shtirnoq argumentni yopib o'z
# ffmpeg parametrlarini qo'shishga, `$VAR` MediaMTX muhit o'zgaruvchisini ochishga,
# boshqa sxemalar (file:, http:, concat:) esa SSRF / lokal fayl o'qishga yo'l ochardi.
# Shuning uchun faqat rtsp:// | rtsps:// va xavfli belgilarsiz (parolda bo'lsa %24 kabi
# URL-kodlanadi). Bo'sh qator — "o'chirish" (update).
# `\Z`, `$` EMAS: Python'da `$` oxirgi "\n" dan oldin ham mos keladi —
# "rtsp://x\n" tekshiruvdan o'tib, yangi qator MediaMTX/ffmpeg buyrug'iga tushardi.
_RTSP_RE = re.compile(r"^rtsps?://[^\s\"'`$\\]+\Z", re.IGNORECASE)


def check_rtsp_url(v: str | None) -> str | None:
    if v is None or v == "":
        return v
    if len(v) > 2000 or not _RTSP_RE.match(v):
        raise ValueError(
            "RTSP URL rtsp:// yoki rtsps:// bilan boshlanishi kerak; bo'sh joy, qo'shtirnoq, "
            "$, \\ va ` bo'lmasin (parolda bo'lsa URL-kodlang: $ → %24)"
        )
    return v


RtspUrl = Annotated[str, AfterValidator(check_rtsp_url)]

Codec = Literal["auto", "h264", "h265"]
SourceType = Literal["rtsp", "ezviz", "isup"]
# ISUP Device ID path nomiga tushadi — faqat xavfsiz belgilar ([[isup.DEVICE_ID_RE]]).
IsupDeviceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Ixtiyoriy hudud (filial ostida). NULL = hech qaysi hududga biriktirilmagan.
    zone_id: uuid.UUID | None = None
    # Manba turi: "rtsp" (oddiy IP kamera) yoki "ezviz" (EZVIZ bulut, RTSP'siz).
    source_type: SourceType = "rtsp"

    # --- RTSP (source_type="rtsp") ---
    rtsp_url: RtspUrl | None = Field(default=None, description="To'liq RTSP (login/parol bilan)")
    rtsp_sub_url: RtspUrl | None = Field(
        default=None, description="Sub-stream (past sifat) RTSP — ABR uchun, ixtiyoriy"
    )

    # --- EZVIZ (source_type="ezviz") ---
    ezviz_account_id: uuid.UUID | None = Field(
        default=None, description="EZVIZ login/parol qaysi hisobdan olinadi"
    )
    ezviz_serial: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ezviz_channel: int = Field(default=1, ge=1, le=64)
    # Shifrlangan (batareyali) kamera "Tasdiqlash Kodu" — ixtiyoriy.
    ezviz_verify_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]{0,64}$")
    # Yo'qotuvchi oqimda buzuq kadrlarni tashlab faqat tozasini ko'rsatish.
    ezviz_drop_corrupt: bool = False
    # Bulut substream (past o'lcham) ni ishlatish — buziladigan yuqori-res kamera uchun.
    ezviz_use_substream: bool = False

    # --- ISUP (source_type="isup") ---
    # Qurilmaning ISUP sozlamasidagi "Device ID" va kanal (NVR'da 1..N).
    isup_device_id: IsupDeviceId | None = None
    isup_channel: int = Field(default=1, ge=1, le=512)
    # Qurilma qaysi kalitda ulanadi (faqat admin): "default" — hudud standarti,
    # <uuid> — hudud ichidagi/umumiy kalit. Berilmasa o'zgarmaydi.
    isup_key: str | None = Field(default=None, pattern=r"^(default|new|[0-9a-fA-F-]{36})$")

    codec: Codec = "auto"
    transcode: bool = False
    enabled: bool = True
    # Doim issiq: tomoshabin bo'lmasa ham oqim 24/7 olinadi (bir zumda ochiladi).
    keep_warm: bool = False

    @model_validator(mode="after")
    def _check_source(self) -> "CameraCreate":
        if self.source_type == "rtsp" and not self.rtsp_url:
            raise ValueError("RTSP kamera uchun rtsp_url majburiy")
        if self.source_type == "isup" and not self.isup_device_id:
            raise ValueError("ISUP kamera uchun isup_device_id majburiy")
        if self.source_type == "ezviz":
            if not self.ezviz_serial:
                raise ValueError("EZVIZ kamera uchun ezviz_serial majburiy")
            if not self.ezviz_account_id:
                raise ValueError("EZVIZ kamera uchun ezviz_account_id majburiy")
        return self


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    zone_id: uuid.UUID | None = None  # boshqa hududga ko'chirish (NULL ham mumkin)
    source_type: SourceType | None = None
    rtsp_url: RtspUrl | None = None
    # "" yoki null -> sub-stream o'chiriladi; berilmasa -> o'zgarmaydi.
    rtsp_sub_url: RtspUrl | None = None
    ezviz_account_id: uuid.UUID | None = None
    ezviz_serial: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ezviz_channel: int | None = Field(default=None, ge=1, le=64)
    # "" -> dekod kodi o'chiriladi; berilmasa -> o'zgarmaydi.
    ezviz_verify_code: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]{0,64}$")
    ezviz_drop_corrupt: bool | None = None
    ezviz_use_substream: bool | None = None
    isup_device_id: IsupDeviceId | None = None
    isup_channel: int | None = Field(default=None, ge=1, le=512)
    isup_key: str | None = Field(default=None, pattern=r"^(default|new|[0-9a-fA-F-]{36})$")
    codec: Codec | None = None
    transcode: bool | None = None
    enabled: bool | None = None
    keep_warm: bool | None = None


class CameraOut(BaseModel):
    """Diqqat: rtsp_url/verify_code HECH QACHON qaytarilmaydi (faqat shifrlangan saqlanadi)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    zone_id: uuid.UUID | None
    name: str
    source_type: str
    ezviz_account_id: uuid.UUID | None
    ezviz_serial: str | None
    ezviz_channel: int
    has_verify_code: bool  # shifrlangan EZVIZ kamera kodi sozlanganmi
    ezviz_drop_corrupt: bool  # buzuq kadrlarni tashlash yoqilganmi
    ezviz_use_substream: bool  # bulut substream (past o'lcham) ishlatilyaptimi
    isup_device_id: str | None
    isup_channel: int
    codec: str
    transcode: bool
    sub_codec: str        # sub-stream kodeki (auto = hali aniqlanmagan)
    sub_transcode: bool   # sub-stream transkod qilinyaptimi (odatda YO'Q — H.264)
    status: str
    last_seen_at: datetime | None
    stream_path: str
    enabled: bool
    has_sub: bool  # ABR sub-stream sozlanganmi
    keep_warm: bool    # panelda "doim issiq" yoqilganmi
    always_warm: bool  # amalda doim issiqmi: keep_warm YOKI ALWAYS_WARM_CAMERA_IDS
    created_at: datetime


class CameraImportRow(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    zone_code: str | None = Field(default=None, max_length=50)  # hudud kodi bo'yicha biriktirish
    rtsp_url: RtspUrl = Field(min_length=1, max_length=2000)
    rtsp_sub_url: RtspUrl | None = Field(default=None, max_length=2000)
    codec: Codec = "auto"
    transcode: bool = False


class CameraImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]


class CameraTestRequest(BaseModel):
    rtsp_url: RtspUrl = Field(min_length=1)


class CameraTestResult(BaseModel):
    ok: bool
    codec: str | None = None
    width: int | None = None
    height: int | None = None


# --- EZVIZ bulut qurilmalarini aniqlash (discovery) ---
class EzvizDevice(BaseModel):
    serial: str
    name: str
    online: bool
    model: str
    channels: int
    already_added: bool  # shu serial bilan kamera allaqachon mavjudmi
    encrypted: bool = False  # shifrlangan — oqim uchun tasdiqlash kodi (verify code) kerak


class EzvizChannel(BaseModel):
    """NVR/qurilmaning ulangan kanali (VTM resurslaridan) — alohida qo'shsa bo'ladi."""

    serial: str
    channel: int
    name: str
    already_added: bool  # shu (serial, kanal) bilan kamera allaqachon mavjudmi
    encrypted: bool = False  # qurilma shifrlangan — tasdiqlash kodi kerak


class EzvizDiscoverResult(BaseModel):
    ok: bool
    devices: list[EzvizDevice] = []
    channels: list[EzvizChannel] = []  # NVR kanallari (VTM resurslaridan, har biri alohida kamera)
    error: str | None = None


# --- ISUP: serverga ulangan qurilmalar (isup-bridge'dan) ---
class IsupStream(BaseModel):
    channel: int
    sub: bool
    live: bool


class IsupDevice(BaseModel):
    device_id: str
    valid: bool               # Device ID'da faqat ruxsat etilgan belgilar (aks holda oqim ochilmaydi)
    serial: str
    model: str                # to'liq seriya (model+sana+seriya)
    ip: str
    firmware: str
    online_seconds: int
    streams: list[IsupStream] = []
    added_channels: list[int] = []  # shu Device ID bilan panelda qo'shilgan kanallar


class IsupDiscoverResult(BaseModel):
    ok: bool
    devices: list[IsupDevice] = []
    # Qurilmaning ISUP sozlamasiga yoziladigan manzil (yo'riqnoma uchun).
    server_host: str = ""
    server_port: int = 17660
    error: str | None = None


class PrewarmRequest(BaseModel):
    """Oldindan issiq tutish uchun kamera id'lari (kamera sahifasi ochilganda)."""

    ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
