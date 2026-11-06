"""Iste'molchi WebSocket — subscribe oqimi va xabar ishlovi (in-memory).

Postgres/Redis'siz: DB helper'lari va MediaMTX monkeypatch qilinadi, Redis
pubsub fake. Maqsad — auth/scope/disabled/xato izolyatsiyasini qulflash.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from vizora.api.integration import ws as consumer

_GOOD_KEY = "good"


class _FakePubSub:
    async def subscribe(self, *a):
        return None

    async def unsubscribe(self, *a):
        return None

    async def aclose(self):
        return None

    async def listen(self):
        # Hech narsa push qilmaymiz — pump bloklanadi, disconnect'da bekor qilinadi.
        import asyncio
        await asyncio.Event().wait()
        yield {}  # erishilmaydi


class _FakeRedis:
    def pubsub(self):
        return _FakePubSub()


def _cam(enabled=True):
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid, enabled=enabled, is_ezviz=False, is_isup=False, stream_path=f"cam/{cid}",
        rtsp_url_enc=b"x", rtsp_sub_url_enc=None, has_sub=False, transcode=False,
        sub_codec="auto", sub_transcode=False, status="online",
    )


@pytest.fixture
def ctx(monkeypatch):
    """(client, registry) — registry'ga fake kamera qo'shib, id'sini WS'ga yuboramiz."""
    registry: dict[str, SimpleNamespace] = {}
    # Mijoz: barcha kameralarga ruxsat (can_access real ishlaydi, scopes all).
    client_obj = SimpleNamespace(id="00000000-0000-0000-0000-0000000000c1", name="t", scopes={"all": True}, enabled=True,
                                 api_key_hash="ab" * 32)

    async def _auth(api_key):
        return client_obj if api_key == _GOOD_KEY else None

    async def _load(cam_id):
        cam = registry.get(cam_id)
        return (cam, None) if cam else (None, None)

    async def _acc(client, zone_id):
        return [{"id": k, "name": "cam"} for k in registry]

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(consumer, "_authenticate", _auth)
    monkeypatch.setattr(consumer, "_load_camera", _load)
    monkeypatch.setattr(consumer, "_accessible_cameras", _acc)
    from vizora.media import mediamtx

    monkeypatch.setattr(mediamtx, "safe_upsert", _noop)
    monkeypatch.setattr(mediamtx, "safe_ensure_path", _noop)
    # crypto'ni MODULNING o'zida patch qilamiz — RTSP URL'ini endi
    # `mediamtx.apply_rtsp_paths` deshifrlaydi (u crypto'ni kech import qiladi).
    from vizora.core import crypto as _crypto

    monkeypatch.setattr(_crypto, "decrypt", lambda b: "rtsp://x")

    app = FastAPI()
    app.include_router(consumer.router)
    app.state.redis = _FakeRedis()
    return TestClient(app), registry


def test_unauthenticated_rejected(ctx):
    client, _ = ctx
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/consumer?api_key=bad") as ws:
            ws.receive_json()


def test_subscribe_stream_ready(ctx):
    client, registry = ctx
    cam = _cam()
    registry[str(cam.id)] = cam
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_json({"type": "subscribe", "camera_id": str(cam.id)})
        msg = ws.receive_json()
    assert msg["type"] == "stream_ready"
    assert msg["camera_id"] == str(cam.id)
    assert msg["hls_url"].startswith(f"/hls/{cam.stream_path}")
    assert msg["token"]


def test_subscribe_disabled_camera(ctx):
    client, registry = ctx
    cam = _cam(enabled=False)
    registry[str(cam.id)] = cam
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_json({"type": "subscribe", "camera_id": str(cam.id)})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["reason"] == "disabled"


def test_subscribe_unknown_camera_not_found(ctx):
    client, _ = ctx
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_json({"type": "subscribe", "camera_id": str(uuid.uuid4())})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["reason"] == "not_found"


def test_no_access_when_scope_excludes(ctx, monkeypatch):
    client, registry = ctx
    # Mijoz scope'ini bo'shatamiz -> can_access False (zone'siz kamera).
    async def _auth_noscope(api_key):
        return SimpleNamespace(id="00000000-0000-0000-0000-0000000000c2", name="t", scopes={}, enabled=True,
                               api_key_hash="cd" * 32)
    monkeypatch.setattr(consumer, "_authenticate", _auth_noscope)
    cam = _cam()
    cam.zone_id = None
    registry[str(cam.id)] = cam
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_json({"type": "subscribe", "camera_id": str(cam.id)})
        msg = ws.receive_json()
    assert msg["type"] == "error" and msg["reason"] == "no_access"


def test_bad_json_and_unknown_type(ctx):
    client, _ = ctx
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_text("{not json")
        assert ws.receive_json()["reason"] == "bad_json"
        ws.send_json({"type": "nonsense"})
        assert ws.receive_json()["reason"] == "unknown_type"


def test_list_and_unsubscribe(ctx):
    client, registry = ctx
    cam = _cam()
    registry[str(cam.id)] = cam
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        ws.send_json({"type": "list"})
        assert ws.receive_json()["type"] == "cameras"
        ws.send_json({"type": "unsubscribe", "camera_id": str(cam.id)})
        m = ws.receive_json()
        assert m["type"] == "unsubscribed"


def test_silent_client_disconnected_when_disabled(ctx, monkeypatch):
    """Xabar yubormaydigan (faqat status oladigan) mijoz ham o'chirilganda uziladi —
    ilgari tekshiruv faqat mijoz xabarida ishlardi (audit 2026-11-03)."""
    client, _ = ctx
    monkeypatch.setattr(consumer, "_RECHECK_SECONDS", 0.05)

    async def _gone(cid):
        return None

    monkeypatch.setattr(consumer, "_reload_client", _gone)
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        assert ws.receive_json()["reason"] == "client_disabled"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_silent_client_disconnected_on_key_rotation(ctx, monkeypatch):
    client, _ = ctx
    monkeypatch.setattr(consumer, "_RECHECK_SECONDS", 0.05)

    async def _rotated(cid):
        return SimpleNamespace(id=cid, name="t", scopes={"all": True}, enabled=True,
                               api_key_hash="ff" * 32)

    monkeypatch.setattr(consumer, "_reload_client", _rotated)
    with client.websocket_connect(f"/ws/consumer?api_key={_GOOD_KEY}") as ws:
        assert ws.receive_json()["reason"] == "client_disabled"
