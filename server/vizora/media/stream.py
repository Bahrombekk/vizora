"""Oqim (HLS) uchun qisqa muddatli, kameraga bog'langan token.

Token JWT bo'lib, `cam` da kamera stream_path ("cam/<id>") saqlanadi. HLS proxy
shu token (yoki admin JWT) bilan kirishga ruxsat beradi. Bir oqim tokeni faqat
o'z kamerasiga yaroqli.

`own` — tokenni KIM olgani (audit 2026-11-03): `c:<ApiClient.id>` (+ `kh` — o'sha
paytdagi API kalit xeshining boshi) yoki `u:<username>` (+ `ver` — token_version).
Oqim endpoint'lari egani bazada tekshiradi ([[media.auth.owner_active]]): mijoz
o'chirilsa, kaliti almashtirilsa yoki admin paroli o'zgarsa token 120 daqiqa
kutmasdan ishlamay qoladi. WS ulanishlar chegarasi ham token emas, ega bo'yicha —
tokenni cheksiz olish mumkin, egani esa yo'q.
"""
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from vizora.core.config import settings

STREAM_EXP_MINUTES = 120


def owner_client(client) -> dict:
    return {"own": f"c:{client.id}", "kh": (client.api_key_hash or "")[:16]}


def owner_user(username: str, token_version: int) -> dict:
    return {"own": f"u:{username}", "ver": int(token_version or 0)}


def make_stream_token(stream_path: str, owner: dict | None = None) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=STREAM_EXP_MINUTES)
    claims = {"cam": stream_path, "typ": "stream", "iat": now, "exp": exp}
    if owner:
        claims.update(owner)
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, exp.isoformat()


def stream_claims(token: str, stream_path: str | None = None) -> dict | None:
    """Imzo, tur va kamera to'g'ri bo'lsa token payload'i, aks holda None."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError:
        return None
    if payload.get("typ") != "stream":
        return None
    if stream_path is not None and payload.get("cam") != stream_path:
        return None
    return payload


def verify_stream_token(token: str, stream_path: str | None = None) -> bool:
    return stream_claims(token, stream_path) is not None


def stream_url(stream_path: str, token: str) -> str:
    """Iste'molchi/Player uchun nisbiy HLS URL (token bilan). Bitta sifatli oqim."""
    return f"/hls/{stream_path}/index.m3u8?token={token}"


def master_url(stream_path: str, token: str) -> str:
    """ABR (HD+SD) master playlist URL — backend hls proxy'da sintez qilinadi."""
    return f"/hls/{stream_path}/master.m3u8?token={token}"


def sub_path(stream_path: str) -> str:
    """Sub-stream uchun MediaMTX path nomi (asosiy path ostida)."""
    return f"{stream_path}/sub"


def hls_url(stream_path: str, token: str, *, has_sub: bool) -> str:
    """Sub-stream bo'lsa ABR master, aks holda oddiy bitta oqim URL'i."""
    return master_url(stream_path, token) if has_sub else stream_url(stream_path, token)


def ws_raw_url(stream_path: str, token: str) -> str:
    """XOM (transkodsiz) oqim URL'i — odatda HEVC, WebCodecs uni o'zi dekodlaydi.

    Faqat EZVIZ kameralarda mavjud (publisher `-c:v copy` bilan qo'shimcha
    chiqish beradi). Brauzer HEVC'ni qo'llamasa pleyer `ws_url` (H.264) ga
    tushadi. [[publish._raw_output]]."""
    return f"/ws/stream/{stream_path}/raw?token={token}"


def ws_url(stream_path: str, token: str, *, sub: bool = False) -> str:
    """WebSocket xom oqim URL'i (WebCodecs pleyer uchun) — bir xil stream token.

    Segment ham, UDP ham yo'q: kodlangan kadrlar WS orqali keladi, brauzer o'zi
    dekodlaydi. `sub=True` — past sifatli (SD) oqim. [[ws_stream]]."""
    return f"/ws/stream/{stream_path}{'/sub' if sub else ''}?token={token}"


def whep_url(stream_path: str, token: str) -> str:
    """WebRTC (WHEP) signalling URL'i — bir xil stream token bilan.

    Brauzer shu manzilga SDP taklifini POST qiladi; [[whep_proxy]] uni MediaMTX'ga
    uzatadi. HLS'dan farqi: segment kutish yo'q — birinchi kadr kelishi bilan
    ko'rsatiladi (ochilish ~2s tezroq, kechikish <1s)."""
    return f"/whep/{stream_path}/whep?token={token}"
