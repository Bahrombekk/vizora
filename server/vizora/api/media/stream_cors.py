"""Oqim yo'llari (/hls, /whep) uchun ochiq CORS — tashqi integratorlar uchun.

NEGA: tashqi tizimlar oqimni O'Z domenidan (boshqa origin) hls.js/fetch bilan
o'qiydi. Bunda brauzer javobda `Access-Control-Allow-Origin` bo'lishini talab
qiladi; bo'lmasa "CORS header missing" bilan bloklaydi (oqimning o'zi 200 bo'lsa
ham). Global `CORSMiddleware` faqat `CORS_ORIGINS` allowlist'iga sarlavha
qo'shadi, ya'ni har integrator domenini oldindan bilish kerak bo'lardi.

Bu yo'llar cookie EMAS, URL'dagi `?token=` (stream JWT, bitta stream_path'ga
bog'langan) bilan avtorizatsiya qilinadi — ya'ni xavfsizlik chegarasi TOKEN,
Origin emas. Shuning uchun har qanday domendan O'QISHGA ruxsat beramiz
(`Access-Control-Allow-Origin: *`, kredensialsiz). Admin panel oqimni bir xil
origin'dan o'ynatadi (CORS umuman tekshirilmaydi), shuning uchun ta'sirlanmaydi.

MUHIM: `/api` (admin, cookie + JWT) bu middleware'ga TEGMAYDI — u global
`CORSMiddleware` bilan qat'iy allowlist'da qoladi (aks holda `allow_credentials`
bilan hamma domenga ochilsa, admin sessiyasi CSRF'ga ochiq bo'lardi).

Pure-ASGI (BaseHTTPMiddleware EMAS): HLS segmentlari `StreamingResponse` bilan
uzatiladi, BaseHTTPMiddleware esa javobni buferlab oqimni buzardi.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

_PREFIXES = ("/hls/", "/whep/")
_STRIP = {b"access-control-allow-origin", b"access-control-allow-credentials"}


class StreamCORSMiddleware:
    """Token bilan himoyalangan oqim yo'llariga origin-agnostik CORS qo'shadi."""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not _is_stream(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        # Preflight (integrator `Authorization` header ishlatsa) — darrov javob.
        if scope.get("method") == "OPTIONS":
            await _send_preflight(send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for (k, v) in message.get("headers", [])
                    if k.lower() not in _STRIP
                ]
                headers.append((b"access-control-allow-origin", b"*"))
                # WHEP mijozi sessiya manzilini `Location` dan o'qiydi (PATCH/DELETE uchun).
                headers.append((b"access-control-expose-headers", b"Location, Link"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _is_stream(path: str) -> bool:
    return any(path.startswith(p) for p in _PREFIXES)


async def _send_preflight(send: Callable[[dict], Awaitable[None]]) -> None:
    headers = [
        (b"access-control-allow-origin", b"*"),
        # WHEP: POST (SDP taklif), PATCH (trickle ICE), DELETE (yopish). Ilgari faqat GET
        # edi va tashqi integrator WHEP'dan umuman foydalana olmasdi (audit 2026-11-03).
        # Kredensial (cookie) ruxsat ETILMAYDI — chegara baribir URL'dagi token.
        (b"access-control-allow-methods", b"GET, POST, PATCH, DELETE, OPTIONS"),
        (b"access-control-allow-headers", b"*"),
        (b"access-control-max-age", b"86400"),
        (b"content-length", b"0"),
    ]
    await send({"type": "http.response.start", "status": 204, "headers": headers})
    await send({"type": "http.response.body", "body": b""})
