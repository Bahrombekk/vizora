"""Kamera oqimlari orkestratori — manba turidan (RTSP / EZVIZ / ISUP) qat'i nazar
"bu kamerani ko'rsatishga tayyorla" degan YAGONA kirish nuqtasi.

Ilgari bu mantiq to'rt joyda (panel `/stream`, iste'molchi REST `/v1/.../stream`,
iste'molchi WS `subscribe`, `prewarm`) va CRUD'da alohida-alohida yozilgan edi —
har biri manba turini o'zi tekshirib, `mediamtx`/`ezviz_manager`/`isup`ni o'zi
chaqirardi, natijada qoidalar asta-sekin bir-biridan ajralib ketgan (masalan,
kamera o'chirilganda `/sub` path'i MediaMTX'da qolib ketardi). Endi:

  configure(cam)      — CRUD/startup: path'larni sozlash yoki (o'chiq bo'lsa) olib tashlash
  apply_changes(cam)  — CRUD'dan keyin: configure + manbaga xos qayta ishga tushirish
  teardown(...)       — kamera o'chirildi/o'chirildi: path'lar va publisher yopiladi
  start(db, cam, ...) — tomoshabin keldi: manbani uyg'otish (+ kerak bo'lsa kutish)
  prewarm(db, cam)    — foydalanuvchi niyati (hover): bloklamasdan uyg'otish
  viewer_links(cam)   — tokenli HLS/WHEP URL'lar (barcha mijozlar uchun bir xil)
  choose_raw(cam,...) — panelga transkodsiz `/raw` faqat u tez ochilsa beriladi
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.config import settings
from vizora.media import mediamtx, signals, stream, ws_stream
from vizora.models.camera import Camera
from vizora.sources import isup
from vizora.sources.ezviz import manager as ezviz_manager

log = logging.getLogger("vizora.streams")


class StreamUnavailable(RuntimeError):
    """Kamera manbasi noto'g'ri sozlangan (masalan EZVIZ hisobi/serial yo'q) —
    mijozga 400 / WS `error` sifatida qaytariladi."""


# --- CRUD / startup ---------------------------------------------------------------

async def configure(cam: Camera) -> None:
    """Kamera holatiga qarab MediaMTX path(lar)ini sozlaydi yoki olib tashlaydi.

    EZVIZ'da RTSP manba yo'q — faqat publishable path yaratiladi (oqimni on-demand
    publisher push qiladi). RTSP/ISUP'da asosiy va (bo'lsa) sub path sozlanadi;
    sub URL o'chirilgan bo'lsa sub path ham olib tashlanadi."""
    if not cam.enabled:
        await teardown(str(cam.id), cam.stream_path, is_ezviz=cam.is_ezviz)
        return
    if cam.is_ezviz:
        await mediamtx.safe_ensure_path(cam.stream_path)
    else:
        await mediamtx.apply_rtsp_paths(cam, prune_sub=True)


async def apply_changes(cam: Camera, *, created: bool = False) -> None:
    """Kamera yaratilgan/tahrirlangandan keyin chaqiriladi."""
    if cam.is_ezviz and not created:
        # Sozlamalar o'zgargan bo'lishi mumkin — joriy publisher to'xtatiladi,
        # keyingi tomosha so'rovida yangi parametrlar bilan qayta boshlanadi.
        await ezviz_manager.stop(str(cam.id))
    await configure(cam)
    if cam.is_isup and cam.enabled:
        await isup.push_keys(force=True)   # qurilma/kalit o'zgargan bo'lishi mumkin
        await isup.touch(cam, wait=False)  # kodek aniqlanishi uchun asosiy oqim ochilsin


async def teardown(camera_id: str, stream_path: str, *, is_ezviz: bool) -> None:
    """Kamera o'chirildi yoki o'chirib qo'yildi: publisher va path'lar yopiladi."""
    if is_ezviz:
        await ezviz_manager.stop(camera_id)
    await mediamtx.remove_path(stream_path)
    await mediamtx.remove_path(stream.sub_path(stream_path))


# --- Tomoshabin yo'li ---------------------------------------------------------------

def raw_eligible(cam: Camera, *, transport: str, hevc: bool) -> bool:
    """Panel transkodsiz `<path>/raw` (odatda HEVC) ni o'qiy oladimi.

    Faqat: WS transporti + brauzer HEVC'ni dekodlay oladi + EZVIZ kamera + xom yo'l
    yoqilgan. Bunda publisher enkodersiz ishlaydi (CPU ~0.05 yadro o'rniga ~1) va
    FAQAT `<path>/raw` mavjud bo'ladi — ws_url ham shu path'ga ishora qilishi shart.
    `drop_corrupt` kamerada oqim PyAV bilan dekodlanadi — passthrough imkonsiz."""
    return bool(
        transport == "ws" and settings.ws_stream_enabled and hevc and cam.is_ezviz
        and settings.ezviz_raw_path and not getattr(cam, "ezviz_drop_corrupt", False)
    )


async def choose_raw(cam: Camera, *, transport: str, hevc: bool, substream: bool = False) -> bool:
    """Panelga xom yo'lni BERISHmi — faqat u tez ochilsa.

    Xom yo'lda brauzer kameraning O'Z navbatdagi I-kadrini kutadi. Past fps'li NVR
    kanallarida I-kadrlar orasi 2-8s, sovuq ochilishda birinchi kadr 12-29s da keldi
    (2026-10-03, 162 kamera bo'yicha o'lchov) — H.264 yo'lida esa enkoder har 1s da
    I-kadr qo'yadi. Shuning uchun xom yo'l faqat:
      * `/raw` hub'i keshida kalit kadr bor (kimdir yaqinda ko'rgan) — darhol ochiladi;
      * XOM publisher allaqachon ishlayapti (hover prewarm) — H.264 so'rash uni qayta
        boshlatardi (~4.6s), bu kutishdan yomonroq;
      * devor plitkasi (substream) — o'nlab plitkaga enkoder ishga tushirib bo'lmaydi.
    Qolgan holatda H.264 (publisher `/raw` ni ham chiqaradi — keyingi ochilish xom)."""
    if not raw_eligible(cam, transport=transport, hevc=hevc):
        return False
    raw_path = f"{cam.stream_path}/raw"
    return (substream
            or ws_stream.has_keyframe(raw_path)
            or ezviz_manager.raw_only_running(str(cam.id))
            # WS hub alohida `media` jarayonida bo'lsa ([[signals.hub_keyframe]])
            or await signals.hub_has_keyframe(raw_path))


def _ezviz_wait(transport: str) -> dict | None:
    """EZVIZ publisher tayyorligini qanday kutish (None = kutilmaydi).

    * ws     — kutilmaydi: WS hub manbani o'zi kutadi va kadr paydo bo'lishi bilan
               uzatadi (ikki marta ketma-ket kutish ~0.5-0.7s bekor vaqt edi).
    * webrtc — path `ready` bo'lishi yetarli, HLS muxerini isitish kerak emas (~2s tejaladi).
    * hls    — path + HLS muxer (pleyer birinchi manifestda 404 olmasin)."""
    if transport == "ws" and settings.ws_stream_enabled:
        return None
    return {"warm_hls": not (transport == "webrtc" and settings.webrtc_enabled)}


async def start(db: AsyncSession, cam: Camera, *, transport: str = "hls",
                need_h264: bool = True, substream: bool = False) -> None:
    """Tomoshabin keldi: kamera manbasini ishga tushiradi (idempotent).

    need_h264=False — mijoz xom oqimni o'zi dekodlaydi (panel WS + HEVC): EZVIZ
                      publisher enkodersiz ishlaydi.
    substream=True  — devor plitkasi: kichik oqim (EZVIZ bulutdan substream so'raladi,
                      ISUP doim ochiq substream'ni o'qiydi)."""
    if cam.is_ezviz:
        await mediamtx.safe_ensure_path(cam.stream_path)
        try:
            await ezviz_manager.ensure_for_camera(db, cam, viewer=True, need_h264=need_h264,
                                                  want_substream=substream)
            wait = _ezviz_wait(transport)
            if wait is not None:
                await ezviz_manager.wait_ready(cam.stream_path, **wait)
        except ezviz_manager.EzvizConfigError as exc:
            raise StreamUnavailable(str(exc)) from exc
        return
    # RTSP / ISUP: path turgani yetarli — MediaMTX `sourceOnDemand` o'zi tortadi.
    # prune_sub yo'q: tomoshabin yo'lida har so'rovda ortiqcha DELETE urmaymiz.
    await mediamtx.apply_rtsp_paths(cam)
    if cam.is_isup:
        # Asosiy oqim faqat so'ralganda ochiladi (~0.3s) — paydo bo'lguncha kutamiz,
        # aks holda transkod ffmpeg'i bo'sh path'ga ulanib chiqib ketadi. Substream
        # 24/7 ochiq — kutish shart emas.
        await isup.touch(cam, wait=not substream)


async def prewarm(db: AsyncSession, cam: Camera) -> None:
    """Foydalanuvchi niyati (kartaga hover): bosishdan oldin manbani uyg'otadi.

    Bloklamaydi — hech narsa kutilmaydi. Panel uchun XOM rejim yetarli (enkodersiz);
    HLS/iste'molchi kelsa publisher o'zi to'liq rejimga o'tadi."""
    if cam.is_ezviz:
        await mediamtx.safe_ensure_path(cam.stream_path)
        try:
            await ezviz_manager.ensure_for_camera(db, cam, viewer=True, need_h264=False)
        except ezviz_manager.EzvizConfigError as exc:
            raise StreamUnavailable(str(exc)) from exc
        return
    await mediamtx.apply_rtsp_paths(cam, prune_sub=True)
    await isup.touch(cam, wait=False)


def viewer_links(cam: Camera, owner: dict | None = None) -> dict:
    """Kameraga bog'langan stream token va u bilan ochiladigan URL'lar.

    Panel, iste'molchi REST va iste'molchi WS — hammasi shu shaklni qaytaradi
    (ustiga o'z maydonlarini qo'shadi). WHEP [[hls_proxy]] bilan AYNI tokenni tekshiradi."""
    token, exp = stream.make_stream_token(cam.stream_path, owner)
    out = {
        "hls_url": stream.hls_url(cam.stream_path, token, has_sub=cam.has_sub),
        "token": token,
        "expires_at": exp,
        "status": cam.status,
        "abr": cam.has_sub,
    }
    if settings.webrtc_enabled:
        out["whep_url"] = stream.whep_url(cam.stream_path, token)
    return out


def ws_links(cam: Camera, token: str, *, raw: bool, substream: bool) -> dict:
    """Panel WS (WebCodecs) URL'lari — `viewer_links` tokeni bilan."""
    if not settings.ws_stream_enabled:
        return {}
    # XOM rejimda publisher FAQAT `<path>/raw` ga yozadi — asosiy path hech
    # qachon tayyor bo'lmaydi, shuning uchun ws_url o'sha yerga ishora qiladi.
    if raw:
        url = stream.ws_raw_url(cam.stream_path, token)
    elif cam.is_isup and substream and cam.has_sub:
        # ISUP substream 24/7 ochiq — devor plitkasi asosiy oqimni uyg'otmaydi.
        url = stream.ws_url(cam.stream_path, token, sub=True)
    else:
        url = stream.ws_url(cam.stream_path, token)
    out = {"ws_url": url, "ws_raw": raw}
    if cam.has_sub and not raw:
        out["ws_sub_url"] = stream.ws_url(cam.stream_path, token, sub=True)
    return out


def has_snapshot_source(cam: Camera) -> bool:
    """Poster olish mumkinmi: EZVIZ — bulut hisobi orqali, RTSP/ISUP — URL'dan bir kadr."""
    if cam.is_ezviz:
        return bool(cam.ezviz_account_id)
    return bool(cam.rtsp_url_enc)
