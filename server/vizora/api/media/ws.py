"""Panel uchun WebSocket xom oqim: /ws/stream/cam/<uuid>[/sub]?token=...

Auth HLS/WHEP proxy bilan bir xil: kameraga bog'langan stream token (`?token=`)
yoki admin JWT (`vz_admin` cookie, faqat panel Origin'idan) — qoidalar `vizora.api.media.auth`da.
Sub-stream asosiy oqim tokeni bilan ochiladi (token `cam/<id>` ga bog'langan).

Mijoz → server xabarlari ixtiyoriy (hozircha faqat "ping"). Server → mijoz:
JSON `init`/`end`, so'ng binary kadrlar ([[ws_stream]] formatida).
"""
from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from vizora.api.media.auth import UUID_RE, owner_active, ws_grant
from vizora.core.config import settings
from vizora.media import ws_stream

log = logging.getLogger("vizora.wsstream")
router = APIRouter()

_PATH = re.compile(rf"^(cam/{UUID_RE})(/sub|/raw)?\Z")   # \Z: `$` oxirgi "\n" dan oldin ham mos keladi


def parse_path(path: str) -> tuple[str, str] | None:
    """`cam/<uuid>[/sub|/raw]` -> (asosiy stream_path, MediaMTX path).

    `/raw` — transkodsiz xom oqim (odatda HEVC), panel pleyeri uchun. Token
    ASOSIY path'ga bog'langan, shuning uchun uchchala variant bir xil token
    bilan ochiladi."""
    m = _PATH.match(path)
    if not m:
        return None
    base = m.group(1).lower()
    return base, base + (m.group(2) or "")


# Chegaralar token EMAS, token EGASI bo'yicha ([[media.auth.principal]]): mijoz yangi
# tokenni cheksiz olishi mumkin, shuning uchun "token boshiga 8" amalda cheklov emas
# edi — tokenlarni ko'paytirib bitta kameraga 64 x 12 MB navbat, kameralar soniga
# ko'paytirib esa server xotirasini tugatish mumkin edi (audit 2026-11-03).
#   * bir ega bir kameraga — ko'pi bilan _MAX_PER_KEY;
#   * bir ega jami — tashqi mijoz _MAX_PER_CLIENT, admin _MAX_PER_ADMIN (6x6 devor
#     + bir nechta brauzer/pleyer).
_MAX_PER_KEY = 8
_MAX_PER_CLIENT = 32
_MAX_PER_ADMIN = 160
_open: dict[tuple[str, str], int] = {}
_open_total: dict[str, int] = {}

# Ochiq ulanishda ega (mijoz yoqilganmi, kaliti/paroli o'zgarmaganmi) shuncha
# soniyada bir qayta tekshiriladi.
OWNER_RECHECK_SECONDS = 30.0


def _total_cap(who: str) -> int:
    return _MAX_PER_ADMIN if who.startswith("u:") else _MAX_PER_CLIENT


def _dec(d: dict, key) -> None:
    n = d.get(key, 1) - 1
    if n > 0:
        d[key] = n
    else:
        d.pop(key, None)


@router.websocket("/ws/stream/{path:path}")
async def ws_stream_endpoint(ws: WebSocket, path: str) -> None:
    parsed = parse_path(path)
    if parsed is None or not settings.ws_stream_enabled:
        await ws.close(code=4404)
        return
    base_path, mtx_path = parsed
    grant = await ws_grant(ws, base_path)
    if grant is None:
        await ws.close(code=4401)
        return
    who, claims = grant
    slot = (who, base_path)
    hub = ws_stream.get_hub(mtx_path)
    if (_open.get(slot, 0) >= _MAX_PER_KEY or _open_total.get(who, 0) >= _total_cap(who)
            or len(hub.subs) >= ws_stream.MAX_SUBS_PER_HUB):
        log.warning("WS ulanishlar chegarasi: %s (%s)", mtx_path, who[:10])
        await ws.close(code=4429)
        return
    _open[slot] = _open.get(slot, 0) + 1
    _open_total[who] = _open_total.get(who, 0) + 1
    await ws.accept()

    sub = hub.subscribe()
    log.info("WS tomoshabin ulandi: %s (jami %d)", mtx_path, len(hub.subs))

    async def pump_out() -> None:
        while True:
            msg = await sub.get()
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_json(msg)
                if msg.get("type") == "end":
                    return

    async def pump_in() -> None:
        # Mijoz yopsa WebSocketDisconnect keladi; matnli xabarlar (ping) yutiladi
        while True:
            await ws.receive_text()

    revoked = False

    async def watch_owner() -> None:
        # Muddat (exp) bu yerda tekshirilmaydi — devor soatlab ochiq turadi; faqat
        # ega bekor qilinganmi: mijoz o'chirilgan/kaliti almashgan, admin paroli o'zgargan.
        nonlocal revoked
        while True:
            await asyncio.sleep(OWNER_RECHECK_SECONDS)
            if not await owner_active(claims):
                revoked = True
                log.warning("WS oqim uzildi — ruxsat bekor qilingan: %s (%s)", mtx_path, who[:10])
                return

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in()),
             asyncio.create_task(watch_owner())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc and not isinstance(exc, WebSocketDisconnect):
                log.debug("WS oqim tugadi (%s): %s", mtx_path, exc)
    finally:
        _dec(_open, slot)
        _dec(_open_total, who)
        hub.unsubscribe(sub)
        log.info("WS tomoshabin uzildi: %s (qoldi %d, tashlangan burst %d)",
                 mtx_path, len(hub.subs), sub.dropped)
        try:
            await ws.close(code=4401 if revoked else 1000)
        except Exception:  # noqa: BLE001
            pass
