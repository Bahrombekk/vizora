"""WHEP (WebRTC) proxy — MediaMTX'ning WebRTC signalling'ini AUTENTIFIKATSIYA bilan.

Nega WebRTC: HLS'da pleyer segmentlarni kutishi SHART (2 × 1s = ~2s) va kechikish
~2-4s bo'ladi. WebRTC'da birinchi kadr kelishi bilan ko'rsatiladi — ochilish
sezilarli tezroq, kechikish <1s (mobil ilova kabi). Iste'molchilar (ApiClient)
HLS'da qoladi (o'zgarmaydi) — bu faqat panel pleyeri uchun.

Oqim himoyalangan: MediaMTX (auth=any) tashqariga OCHILMAYDI, brauzer faqat shu
proxy orqali o'tadi va [[hls_proxy]] bilan AYNI stream token'ni ishlatadi.

WHEP protokoli (MediaMTX v1.19):
    POST   /<path>/whep              (Content-Type: application/sdp)  -> 201 + SDP javob
                                     Location: /<path>/whep/<session-uuid>
    PATCH  /<path>/whep/<session>    (trickle ICE nomzodlari)         -> 204
    DELETE /<path>/whep/<session>    (sessiyani yopish)               -> 200
MediaMTX bergan `Location`ni O'ZIMIZGA qayta yozamiz, aks holda brauzer keyingi
so'rovlarni to'g'ridan MediaMTX'ga (yopiq port) yuborardi.
"""
from __future__ import annotations

import logging
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from vizora.core.config import settings
from vizora.api.media.auth import UUID_RE, authorize_http
from vizora.media import mediamtx

log = logging.getLogger("vizora.whep")

router = APIRouter(tags=["whep"])

_SDP = "application/sdp"

# `cam/<uuid>[/sub]/whep[/<session-uuid>]` — QAT'IY. Xom mijoz yo'lini upstream'ga
# hech qachon o'zi holida bermaymiz (path traversal / boshqa path'ga kirishga qarshi).
_PATH_RE = re.compile(rf"^(cam/{UUID_RE})(/sub)?/whep(?:/({UUID_RE}))?\Z")

# SDP taklif / trickle ICE — bir necha KB. Ilgari tana cheksiz o'qilib xotiraga
# olinardi (nginx 16 MB gacha o'tkazadi), keyin MediaMTX'ga uzatilardi.
_MAX_BODY = 64 * 1024


class _TooLarge(Exception):
    pass


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_BODY:
        raise _TooLarge
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > _MAX_BODY:
            raise _TooLarge
    return bytes(buf)


def _too_large() -> JSONResponse:
    return JSONResponse({"detail": "So'rov tanasi juda katta"}, status_code=413)


def _parse(path: str) -> tuple[str, str] | None:
    """`(stream_path, mtx_path)` qaytaradi yoki None (yaroqsiz yo'l).

    `stream_path` — token tekshiruvi uchun KAMERA path'i (`cam/<id>`, `/sub` siz):
    stream token asosiy kamera path'iga beriladi, SD variant ham shu ruxsat ostida.
    """
    m = _PATH_RE.match(path)
    if m is None:
        return None
    cam_path, sub, session = m.group(1), m.group(2) or "", m.group(3)
    mtx = f"{cam_path}{sub}/whep" + (f"/{session}" if session else "")
    return cam_path, mtx


def _upstream(mtx_path: str) -> str:
    return f"{settings.mediamtx_webrtc_url}/{mtx_path}"


async def _guard(path: str, request: Request):
    """Yo'lni tekshirib, ruxsatni tasdiqlaydi. Xato bo'lsa javob, aks holda (paths)."""
    if not settings.webrtc_enabled:
        return JSONResponse({"detail": "WebRTC o'chirilgan"}, status_code=404), None
    parsed = _parse(path)
    if parsed is None:
        return JSONResponse({"detail": "Topilmadi"}, status_code=404), None
    stream_path, mtx_path = parsed
    if await authorize_http(request, stream_path) is None:
        return JSONResponse({"detail": "Oqimga ruxsat yo'q"}, status_code=401), None
    return None, mtx_path


@router.post("/whep/{path:path}")
async def whep_offer(path: str, request: Request):
    """SDP taklifini MediaMTX'ga uzatib, SDP javobini qaytaradi."""
    err, mtx_path = await _guard(path, request)
    if err is not None:
        return err
    try:
        offer = await _read_body(request)
    except _TooLarge:
        return _too_large()
    try:
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=20.0) as c:
            up = await c.post(_upstream(mtx_path), content=offer,
                              headers={"Content-Type": _SDP})
    except httpx.HTTPError as exc:
        log.warning("WHEP upstream xatosi (%s): %s", mtx_path, exc)
        return JSONResponse({"detail": "Oqim mavjud emas"}, status_code=503)

    headers: dict[str, str] = {"Cache-Control": "no-store"}
    # Location'ni O'ZIMIZGA yo'naltiramiz: MediaMTX `/cam/<id>/whep/<sess>` beradi,
    # brauzer esa `/whep/cam/<id>/whep/<sess>` ga borishi kerak (token bilan).
    loc = up.headers.get("location")
    if loc:
        token = request.query_params.get("token")
        q = f"?token={token}" if token else ""
        headers["Location"] = f"/whep/{loc.lstrip('/')}{q}"
    # ICE server'lar (Link) bo'lsa o'zgarmagan holda uzatamiz.
    if "link" in up.headers:
        headers["Link"] = up.headers["link"]
    return Response(content=up.content, status_code=up.status_code, headers=headers,
                    media_type=up.headers.get("content-type") or _SDP)


@router.patch("/whep/{path:path}")
async def whep_patch(path: str, request: Request):
    """Trickle ICE — brauzerning qo'shimcha nomzodlarini uzatadi."""
    err, mtx_path = await _guard(path, request)
    if err is not None:
        return err
    try:
        body = await _read_body(request)
    except _TooLarge:
        return _too_large()
    ctype = request.headers.get("content-type", "application/trickle-ice-sdpfrag")
    try:
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=15.0) as c:
            up = await c.patch(_upstream(mtx_path), content=body,
                               headers={"Content-Type": ctype})
    except httpx.HTTPError:
        return Response(status_code=503)
    # Sessiya negociatsiya tugamasdan o'lgan bo'lsa MediaMTX 404 beradi (ICE
    # ulanmagan muhitda odatiy). Trickle ICE endi keraksiz — jimgina 204.
    if up.status_code == 404:
        return Response(status_code=204)
    return Response(content=up.content, status_code=up.status_code,
                    media_type=up.headers.get("content-type"))


@router.delete("/whep/{path:path}")
async def whep_delete(path: str, request: Request):
    """Sessiyani yopadi (brauzer sahifadan chiqqanda) — resurs bo'sh qoladi.

    IDEMPOTENT: allaqachon yopilgan sessiyani o'chirish = MUVAFFAQIYAT. MediaMTX
    404 qaytaradi agar sessiya YO'Q bo'lsa (masalan ICE ulanmay vaqt tugashidan
    keyin MediaMTX o'zi o'chirgan bo'lsa — WebRTC UDP porti yopiq muhitda odatiy
    holat). Bu XATO emas: teardown maqsadi "sessiya yo'q bo'lsin" edi, u yo'q.
    Shuning uchun 404 ni ham 204 (muvaffaqiyat) qilib qaytaramiz — brauzerда
    keraksiz qizil 404 ko'rinmasin."""
    err, mtx_path = await _guard(path, request)
    if err is not None:
        return err
    try:
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=10.0) as c:
            up = await c.delete(_upstream(mtx_path))
    except httpx.HTTPError:
        return Response(status_code=204)   # yetib bo'lmadi — baribir mijoz uchun tugadi
    # 2xx (o'chirildi) yoki 404 (allaqachon yo'q) -> 204. Boshqa xato -> o'zini uzatamiz.
    if up.status_code < 300 or up.status_code == 404:
        return Response(status_code=204)
    return Response(status_code=up.status_code)


@router.options("/whep/{path:path}")
async def whep_options(path: str) -> Response:
    """Brauzer preflight'i — bir xil origin bo'lsa ham ba'zi holatda so'raladi."""
    return Response(status_code=204, headers={
        "Access-Control-Allow-Methods": "OPTIONS, POST, PATCH, DELETE",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    })
