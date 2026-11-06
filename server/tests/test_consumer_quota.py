"""API mijoz kamera kvotasi (audit 2026-11-03): bitta kalit hamma kamerani birdan
transkodga majburlay olmaydi."""
import pytest

from vizora.core.config import settings
from vizora.services import consumer_quota


class _ZRedis:
    def __init__(self):
        self.z: dict[str, dict[str, float]] = {}

    async def zremrangebyscore(self, k, lo, hi):
        d = self.z.get(k, {})
        for m in [m for m, s in d.items() if lo <= s <= hi]:
            del d[m]

    async def zscore(self, k, m):
        return self.z.get(k, {}).get(m)

    async def zcard(self, k):
        return len(self.z.get(k, {}))

    async def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)

    async def expire(self, k, s):
        return True


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setattr(settings, "consumer_max_cameras", 3)
    monkeypatch.setattr(settings, "consumer_camera_window_seconds", 300)


async def test_distinct_camera_limit():
    r = _ZRedis()
    for cam in ("a", "b", "c"):
        assert await consumer_quota.allow_camera(r, "client1", cam)
    assert not await consumer_quota.allow_camera(r, "client1", "d")
    # o'sha kamerani qayta so'rash kvotani yemaydi
    assert await consumer_quota.allow_camera(r, "client1", "a")
    # boshqa mijozning kvotasi alohida
    assert await consumer_quota.allow_camera(r, "client2", "d")


async def test_window_expiry(monkeypatch):
    r = _ZRedis()
    t = [1000.0]
    monkeypatch.setattr(consumer_quota.time, "time", lambda: t[0])
    for cam in ("a", "b", "c"):
        await consumer_quota.allow_camera(r, "c1", cam)
    assert not await consumer_quota.allow_camera(r, "c1", "d")
    t[0] += 301
    assert await consumer_quota.allow_camera(r, "c1", "d")


async def test_redis_failure_allows():
    class _Broken:
        async def zremrangebyscore(self, *a):
            raise ConnectionError("redis yo'q")

    assert await consumer_quota.allow_camera(_Broken(), "c1", "x")
    assert await consumer_quota.allow_camera(None, "c1", "x")
