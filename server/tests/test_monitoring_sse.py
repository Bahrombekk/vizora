"""Monitoring SSE — Redis `camera:status` hodisasi oqimga chiqishini tekshiradi.

StreamingResponse'ning body_iterator'ini to'g'ridan-to'g'ri iteratsiya qilamiz
(TestClient cheksiz oqimni toza yopa olmaydi — teardown osilib qoladi)."""
import json
from types import SimpleNamespace

from vizora.api.panel import monitoring


class _FakePubSub:
    def __init__(self):
        self._sent = False

    async def subscribe(self, *a):
        return None

    async def unsubscribe(self, *a):
        return None

    async def aclose(self):
        return None

    async def get_message(self, ignore_subscribe_messages=False, timeout=None):
        if not self._sent:
            self._sent = True
            return {"type": "message", "data": json.dumps({"camera_id": "cam-1", "status": "online"})}
        return None  # keyin heartbeat


class _FakeRedis:
    def pubsub(self):
        return _FakePubSub()


def _fake_request():
    async def _not_disconnected():
        return False

    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=_FakeRedis())))
    req.is_disconnected = _not_disconnected
    return req


async def test_sse_streams_status_event():
    resp = await monitoring.status_stream(_fake_request(), None)
    assert resp.media_type == "text/event-stream"
    it = resp.body_iterator
    chunks = []
    try:
        for _ in range(5):
            chunks.append(await anext(it))
            if chunks[-1].startswith("data:"):
                break
    finally:
        await it.aclose()  # generator finally -> unsubscribe/aclose
    data_line = next(c for c in chunks if c.startswith("data:"))
    got = json.loads(data_line[len("data:"):].strip())
    assert got == {"camera_id": "cam-1", "status": "online"}
