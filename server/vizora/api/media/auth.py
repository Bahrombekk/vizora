"""Oqim endpoint'lari (/hls, /whep, /ws/stream) uchun YAGONA ruxsat qoidalari.

Ilgari WHEP proxy HLS modulining xususiy `_authorize`ini import qilardi, WS esa o'z
nusxasini yozgan edi — qoida o'zgarsa uch joyni birdek tuzatish kerak bo'lardi.

Qoidalar:
  * stream token (`typ:"stream"`) — BITTA kamera path'iga bog'langan; bir kamera
    tokeni boshqasini ko'rsatmaydi. Tokenda egasi (`own`) bo'lsa, ega bazada ham
    tekshiriladi ([[owner_active]]);
  * admin JWT — faqat `Authorization: Bearer` (HTTP) yoki panelning o'z sahifasidan
    kelgan `vz_admin` cookie (WS). URL'da (`?token=`) QABUL QILINMAYDI — nginx/uvicorn
    access loglariga tushardi.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass

from fastapi import Request, WebSocket
from sqlalchemy import select

from vizora.api import deps as _deps
from vizora.api.deps import ADMIN_COOKIE, origin_allowed
from vizora.core.db import SessionLocal
from vizora.core.security import decode_access_token
from vizora.media.stream import owner_user, stream_claims
from vizora.models.client import ApiClient

STREAM_COOKIE = "vz_stream"

# Kamera/sessiya id'lari — QAT'IY UUID (greedy-prefix moslashuvga qarshi).
UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# Ega tekshiruvi natijasi shuncha soniya keshlanadi: HLS pleyer soniyasiga bir necha
# segment/part so'raydi — har biriga DB so'rovi bo'lmasin. Bekor qilish shu muddat
# ichida kuchga kiradi.
OWNER_CACHE_SECONDS = 15.0
_owner_cache: dict[tuple, tuple[float, bool]] = {}


@dataclass(frozen=True)
class StreamGrant:
    """Oqim so'roviga berilgan ruxsat.

    cookie — javobga `vz_stream` sifatida yoziladigan qiymat ("" — yangilanmaydi);
    token  — ruxsat bergan STREAM token'ning o'zi. Playlist'larga AYNAN shu token
             qo'yiladi: yangisini mint qilish uning muddatini uzaytirardi — eski
             token bilan `master.m3u8` ni har 100 daqiqada so'rab, o'chirilgan
             ApiClient ham oqimni abadiy ko'ra olardi (audit 2026-11-03).
             Admin JWT bilan kirilganda None (admin JWT playlist tanasiga tushmasin).
    owner  — admin JWT bilan kirilganda yangi token egasi (mint uchun), aks holda None.
    """
    cookie: str
    token: str | None
    owner: dict | None = None


async def _client_row(client_id: str):
    """(enabled, api_key_hash) — yoki None. Testlarda almashtiriladi."""
    try:
        cid = uuid.UUID(client_id)
    except ValueError:
        return None
    async with SessionLocal() as db:
        return (await db.execute(
            select(ApiClient.enabled, ApiClient.api_key_hash).where(ApiClient.id == cid)
        )).first()


async def _owner_check(claims: dict) -> bool:
    own = str(claims.get("own") or "")
    kind, _, ident = own.partition(":")
    if kind == "c":
        row = await _client_row(ident)
        kh = str(claims.get("kh") or "")
        return bool(row is not None and row.enabled and kh
                    and (row.api_key_hash or "").startswith(kh))
    if kind == "u":
        row = await _deps._user_row(ident)
        return bool(row is not None and row.role
                    and int(claims.get("ver", 0) or 0) == int(row.token_version or 0))
    return False


async def owner_active(claims: dict) -> bool:
    """Token egasi hali ham huquqli: mijoz yoqilgan va kaliti o'sha, yoki admin
    paroli (token_version) o'zgarmagan. Egasiz eski tokenlar (deploy'dan oldin
    berilgan, ko'pi bilan 120 daqiqa) avvalgidek o'tadi."""
    if not claims.get("own"):
        return True
    key = (claims.get("own"), claims.get("kh"), claims.get("ver"))
    now = time.monotonic()
    hit = _owner_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    ok = await _owner_check(claims)
    if len(_owner_cache) > 4096:
        _owner_cache.clear()
    _owner_cache[key] = (now + OWNER_CACHE_SECONDS, ok)
    return ok


async def valid_stream_token(token: str | None, stream_path: str | None) -> dict | None:
    if not token:
        return None
    claims = stream_claims(token, stream_path)
    if claims is None or not await owner_active(claims):
        return None
    return claims


def principal(claims: dict, token: str) -> str:
    """WS ulanishlar chegarasi kaliti: token egasi (bo'lsa), aks holda token xeshi."""
    own = claims.get("own")
    if own:
        return str(own)
    return "t:" + hashlib.sha256(token.encode()).hexdigest()[:16]


async def admin_payload(token: str) -> dict | None:
    """Admin JWT'ni imzo VA bazadagi holat bo'yicha tekshiradi.

    Faqat imzo yetarli emas: `get_current_user` kabi foydalanuvchi mavjudligi va
    `token_version` solishtiriladi — aks holda parol almashgandan keyin ham
    o'g'irlangan cookie barcha kameralarni ko'rib turardi (audit 2026-11-03)."""
    payload = decode_access_token(token)
    if not payload or not payload.get("role"):
        return None
    row = await _deps._user_row(payload.get("sub", ""))
    if row is None or not row.role:
        return None
    if int(payload.get("ver", 0) or 0) != int(row.token_version or 0):
        return None
    return payload


async def is_admin(token: str) -> bool:
    return await admin_payload(token) is not None


async def authorize_http(request: Request, stream_path: str | None) -> StreamGrant | None:
    """HLS/WHEP so'rovi uchun ruxsat ([[StreamGrant]]) yoki None.

    Tartib: cookie → `?token=` (faqat stream token) → `Authorization: Bearer`
    (stream token yoki admin)."""
    cookie = request.cookies.get(STREAM_COOKIE)
    if await valid_stream_token(cookie, stream_path):
        return StreamGrant(cookie="", token=cookie)
    qtok = request.query_params.get("token")
    if await valid_stream_token(qtok, stream_path):
        return StreamGrant(cookie=qtok, token=qtok)  # cookie sifatida o'rnatamiz
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        btok = auth[7:]
        if await valid_stream_token(btok, stream_path):
            return StreamGrant(cookie="", token=btok)
        p = await admin_payload(btok)
        if p is not None:
            return StreamGrant(cookie="", token=None,
                               owner=owner_user(p.get("sub", ""), p.get("ver", 0)))
    return None


async def ws_grant(ws: WebSocket, base_path: str) -> tuple[str, dict] | None:
    """WS xom oqim uchun (ruxsat kaliti, ega claim'lari) yoki None.

    * stream token (`?token=`) — shu kameraga bog'langan, Origin'dan qat'i nazar;
      kalit — token EGASI ([[principal]]);
    * admin cookie — FAQAT panelning o'z sahifasidan (Origin), aks holda qo'shni
      subdomendagi sahifa admin nomidan istalgan kamerani ocha olardi; sessiya
      bazada ham tekshiriladi ([[admin_payload]]).

    Claim'lar ulanish davomida [[owner_active]] bilan qayta tekshiriladi — ochiq
    qolgan WS mijoz o'chirilgandan keyin ham soatlab video olib turmasin."""
    tok = ws.query_params.get("token")
    claims = await valid_stream_token(tok, base_path)
    if claims is not None:
        return principal(claims, tok), claims
    admin = ws.cookies.get(ADMIN_COOKIE)
    if admin and origin_allowed(ws.headers):
        p = await admin_payload(admin)
        if p is not None:
            return "u:" + str(p.get("sub", "")), owner_user(p.get("sub", ""), p.get("ver", 0))
    return None


async def authorize_ws(ws: WebSocket, base_path: str) -> str | None:
    """[[ws_grant]] ning faqat kaliti (ulanishlar sonini cheklash uchun)."""
    g = await ws_grant(ws, base_path)
    return g[0] if g else None
