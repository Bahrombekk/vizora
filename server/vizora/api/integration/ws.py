"""Iste'molchi WebSocket API — tashqi tizimlar kamerani so'rab oqim oladi.

Ulanish:  wss://server/ws/consumer?api_key=<key>

Mijoz → Server:
  {"type":"subscribe",   "camera_id": "<uuid>"}
  {"type":"unsubscribe", "camera_id": "<uuid>"}
  {"type":"list",        "zone_id": "<uuid>"?}   # ruxsat etilgan kameralar
  {"type":"status",      "camera_id": "<uuid>"}

Server → Mijoz:
  {"type":"stream_ready",   "camera_id", "hls_url", "token", "expires_at", "status"}
  {"type":"status_changed", "camera_id", "status"}   # real vaqt push (Redis pub/sub)
  {"type":"cameras", "items":[...]}                  # list javobi
  {"type":"error",   "reason": "..."}
"""
import asyncio
import contextlib
import json
import logging
import time
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from vizora.core.db import SessionLocal
from vizora.core.security import hash_api_key
from vizora.models.camera import Camera
from vizora.models.client import ApiClient
from vizora.models.zone import Zone
from vizora.media import stream
from vizora.services import camera_streams, consumer_quota
from vizora.services.access import can_access
from vizora.services.probe import STATUS_CHANNEL

log = logging.getLogger("vizora.ws")
router = APIRouter()

# Bitta ulanish uchun maksimal obuna (resurs cheklash).
_MAX_SUBS = 200
# Bitta API mijozning bir vaqtdagi WS ulanishlari (har biri Redis pubsub ochadi).
_MAX_CONNS_PER_CLIENT = 5
_MAX_MSG_BYTES = 64 * 1024
# Mijoz o'chirilsa / scope toraytirilsa ochiq ulanish ham shu oraliqda sezadi.
_RECHECK_SECONDS = 30
_conns: dict[str, int] = {}


def _norm_id(raw: object) -> str | None:
    """camera_id'ni kanonik UUID string'ga keltiradi (yaroqsiz bo'lsa None).

    Subscribe `str(cam.id)` saqlaydi, status-pump ham shu shaklni kutadi —
    unsubscribe/status ham bir xil normallashtirilishi shart, aks holda mos
    kelmaydi."""
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        return None


async def _authenticate(api_key: str | None) -> ApiClient | None:
    if not api_key:
        return None
    h = hash_api_key(api_key)
    async with SessionLocal() as db:
        client = (
            await db.execute(select(ApiClient).where(ApiClient.api_key_hash == h))
        ).scalar_one_or_none()
    if client is None or not client.enabled:
        return None
    return client


async def _reload_client(cid: str) -> ApiClient | None:
    async with SessionLocal() as db:
        c = await db.get(ApiClient, uuid.UUID(cid))
    return c if c is not None and c.enabled else None


async def _load_camera(camera_id: str) -> tuple[Camera, Zone] | tuple[None, None]:
    async with SessionLocal() as db:
        cam = await db.get(Camera, camera_id)
        if cam is None:
            return None, None
        fac = await db.get(Zone, cam.zone_id) if cam.zone_id else None
        return cam, fac


async def _accessible_cameras(client: ApiClient, zone_id: str | None):
    async with SessionLocal() as db:
        # O'chirilgan kameralar ro'yxatga KIRMAYDI (REST /api/v1/cameras bilan bir xil).
        stmt = (select(Camera, Zone).outerjoin(Zone, Camera.zone_id == Zone.id)
                .where(Camera.enabled.is_(True)))
        if zone_id:
            stmt = stmt.where(Camera.zone_id == zone_id)
        rows = (await db.execute(stmt)).all()
    out = []
    for cam, fac in rows:
        if can_access(client, cam, fac):
            out.append({
                "id": str(cam.id), "name": cam.name,
                "zone_id": str(cam.zone_id) if cam.zone_id else None,
                "status": cam.status, "codec": cam.codec, "enabled": cam.enabled,
            })
    return out


@router.websocket("/ws/consumer")
async def consumer_ws(websocket: WebSocket, api_key: str | None = Query(default=None)):
    client = await _authenticate(api_key)
    if client is None:
        await websocket.close(code=4401)  # Unauthorized
        return
    cid = str(client.id)
    if _conns.get(cid, 0) >= _MAX_CONNS_PER_CLIENT:
        log.warning("Iste'molchi %s: WS ulanishlar chegarasi (%d)", client.name, _MAX_CONNS_PER_CLIENT)
        await websocket.close(code=4429)
        return
    _conns[cid] = _conns.get(cid, 0) + 1
    last_check = time.monotonic()
    await websocket.accept()
    log.info("Iste'molchi ulandi: %s", client.name)

    subscribed: set[str] = set()
    redis = websocket.app.state.redis
    pubsub = redis.pubsub()
    await pubsub.subscribe(STATUS_CHANNEL)

    # Barcha send'lar bitta lock orqali — pump va asosiy loop bir vaqtda
    # yozmasin (Starlette WS concurrent-send'ni qo'llamaydi).
    send_lock = asyncio.Lock()

    async def send(payload: dict) -> bool:
        """Soketga yozadi; soket yopilgan bo'lsa False (chaqiruvchi to'xtaydi)."""
        async with send_lock:
            try:
                await websocket.send_json(payload)
                return True
            except Exception:  # noqa: BLE001 — yopilgan/uzilgan soket
                return False

    async def pump_status() -> None:
        """Redis status push'larini uzatadi. Bitta xato butun pump'ni
        (va status push'larni) jimgina o'ldirmasligi uchun himoyalangan."""
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except (ValueError, TypeError):
                    continue
                if data.get("camera_id") in subscribed:
                    if not await send({"type": "status_changed", **data}):
                        return  # soket yopilgan
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("WS status pump to'xtadi (%s): %s", client.name, exc)

    pump_task = asyncio.create_task(pump_status())
    key_hash = client.api_key_hash

    async def watch_client() -> None:
        """Mijozni FONDA qayta tekshiradi. Ilgari faqat mijoz xabar yuborganda
        tekshirilardi — jim turgan (faqat status push oladigan) mijoz o'chirilgandan
        yoki kaliti almashtirilgandan keyin ham status olib turardi (audit 2026-11-03)."""
        nonlocal client
        while True:
            await asyncio.sleep(_RECHECK_SECONDS)
            fresh = await _reload_client(cid)
            if fresh is None or fresh.api_key_hash != key_hash:
                subscribed.clear()
                pump_task.cancel()
                log.warning("Iste'molchi %s: ruxsat bekor qilingan — WS uziladi", client.name)
                await send({"type": "error", "reason": "client_disabled"})
                with contextlib.suppress(Exception):
                    await websocket.close(code=4401)
                return
            client = fresh

    watch_task = asyncio.create_task(watch_client())

    async def _handle(req: dict) -> None:
        mtype = req.get("type")
        cam_id = _norm_id(req.get("camera_id"))

        if mtype == "subscribe":
            cam, fac = await _load_camera(cam_id) if cam_id else (None, None)
            if cam is None:
                await send({"type": "error", "camera_id": cam_id, "reason": "not_found"})
            elif not can_access(client, cam, fac):
                await send({"type": "error", "camera_id": cam_id, "reason": "no_access"})
            elif not cam.enabled:
                await send({"type": "error", "camera_id": cam_id, "reason": "disabled"})
            elif len(subscribed) >= _MAX_SUBS and str(cam.id) not in subscribed:
                await send({"type": "error", "camera_id": cam_id, "reason": "too_many_subscriptions"})
            elif not await consumer_quota.allow_camera(redis, client.id, cam.id):
                await send({"type": "error", "camera_id": cam_id, "reason": "too_many_cameras"})
            else:
                # Manbani uyg'otib, HLS tayyor bo'lguncha kutamiz (EZVIZ publisher /
                # ISUP asosiy oqim / RTSP path) — keyin obuna qilamiz.
                async with SessionLocal() as db:
                    await camera_streams.start(db, cam)
                subscribed.add(str(cam.id))
                await send({"type": "stream_ready", "camera_id": str(cam.id),
                            **camera_streams.viewer_links(cam, stream.owner_client(client))})

        elif mtype == "unsubscribe":
            subscribed.discard(cam_id)
            await send({"type": "unsubscribed", "camera_id": cam_id})

        elif mtype == "list":
            items = await _accessible_cameras(client, _norm_id(req.get("zone_id")))
            await send({"type": "cameras", "items": items})

        elif mtype == "status":
            cam, fac = await _load_camera(cam_id) if cam_id else (None, None)
            if cam is None or not cam.enabled or not can_access(client, cam, fac):
                await send({"type": "error", "camera_id": cam_id, "reason": "no_access"})
            else:
                await send({"type": "status_changed", "camera_id": str(cam.id), "status": cam.status})

        else:
            await send({"type": "error", "reason": "unknown_type"})

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > _MAX_MSG_BYTES:
                await send({"type": "error", "reason": "too_large"})
                continue
            # Mijoz o'chirilgan yoki scope'i o'zgargan bo'lishi mumkin — vaqti-vaqti bilan
            # bazadan qayta o'qiymiz (avval faqat ulanishda tekshirilardi).
            if time.monotonic() - last_check > _RECHECK_SECONDS:
                last_check = time.monotonic()
                fresh_client = await _reload_client(cid)
                if fresh_client is None:
                    await send({"type": "error", "reason": "client_disabled"})
                    await websocket.close(code=4401)
                    break
                client = fresh_client
            try:
                req = json.loads(raw)
            except ValueError:
                await send({"type": "error", "reason": "bad_json"})
                continue
            if not isinstance(req, dict):
                await send({"type": "error", "reason": "bad_json"})
                continue
            # Bitta xabar ishlovidagi kutilmagan xato (masalan rotated AES kalit
            # bilan crypto.decrypt) BUTUN soketni qulatmasin — log + error + davom.
            try:
                await _handle(req)
            except camera_streams.StreamUnavailable as exc:
                await send({"type": "error", "camera_id": _norm_id(req.get("camera_id")), "reason": str(exc)})
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("WS xabar ishlovida xato (%s): %s", client.name, exc)
                await send({"type": "error", "reason": "internal_error"})

    except WebSocketDisconnect:
        log.info("Iste'molchi uzildi: %s", client.name)
    finally:
        left = _conns.get(cid, 1) - 1
        if left > 0:
            _conns[cid] = left
        else:
            _conns.pop(cid, None)
        pump_task.cancel()
        watch_task.cancel()
        try:
            await pubsub.unsubscribe(STATUS_CHANNEL)
            await pubsub.aclose()
        except Exception:  # noqa: BLE001
            pass
