"""API <-> media jarayoni signallari (Redis). Video alohida jarayonga ajratilgach
uch narsa xotira orqali emas, shu yerdan o'tadi."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from vizora.media import signals
from vizora.sources.ezviz import manager as mgr


class _R:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def set(self, k, v, ex=None):
        self.kv[k] = v

    async def delete(self, k):
        self.kv.pop(k, None)

    async def exists(self, k):
        return int(k in self.kv)


@pytest.fixture(autouse=True)
def redis():
    r = _R()
    signals.init(r)
    yield r
    signals.init(None)


async def test_publisher_dead_roundtrip():
    assert not await signals.publisher_dead("cam/a")
    await signals.publisher_exited("cam/a")
    assert await signals.publisher_dead("cam/a")
    await signals.publisher_started("cam/a")          # yangi publisher belgini o'chiradi
    assert not await signals.publisher_dead("cam/a")


async def test_viewer_and_keyframe():
    await signals.touch_viewer("cam/a")
    assert await signals.viewer_recent("cam/a")
    assert not await signals.viewer_recent("cam/b")
    await signals.hub_keyframe("cam/a/raw", True)
    assert await signals.hub_has_keyframe("cam/a/raw")
    await signals.hub_keyframe("cam/a/raw", False)
    assert not await signals.hub_has_keyframe("cam/a/raw")


async def test_no_redis_means_unknown():
    signals.init(None)
    await signals.publisher_exited("cam/a")
    assert not await signals.publisher_dead("cam/a")


class _Proc:
    def __init__(self):
        self.returncode = None
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def exit(self, rc):
        self.returncode = rc
        self._done.set()


async def test_unexpected_exit_is_signalled(monkeypatch):
    """Ro'yxatdagi publisher o'lsa — media jarayoni buni Redis'dan biladi."""
    proc = _Proc()
    pub = mgr._Publisher(proc, "cam/x", "ezviz")
    monkeypatch.setitem(mgr._publishers, "x", pub)
    t = asyncio.create_task(mgr._watch_exit("x", pub))
    proc.exit(3)
    await t
    assert await signals.publisher_dead("cam/x")


async def test_deliberate_stop_is_not_dead(monkeypatch):
    """`stop()` avval ro'yxatdan oladi — ataylab to'xtatilgan publisher "o'lgan" emas
    (aks holda H.264 ga qayta ishga tushirishda hub tomoshabinni uzib yuborardi)."""
    proc = _Proc()
    pub = mgr._Publisher(proc, "cam/y", "ezviz")
    t = asyncio.create_task(mgr._watch_exit("y", pub))   # ro'yxatda yo'q
    proc.exit(0)
    await t
    assert not await signals.publisher_dead("cam/y")


async def test_reaper_keeps_publisher_while_media_hub_waits(monkeypatch):
    """MediaMTX'da reader yo'q (manba hali ulanmoqda), lekin media jarayonidagi hub
    kutmoqda — reaper publisher'ni o'ldirmasligi kerak."""
    proc = _Proc()
    pub = mgr._Publisher(proc, "cam/z", "ezviz")
    pub.last_request = time.monotonic() - 10_000
    monkeypatch.setattr(mgr, "_publishers", {"z": pub})
    monkeypatch.setattr(mgr, "_always_ids", lambda: set())

    async def _no_readers():
        return {}

    stopped = []

    async def _stop(cid):
        stopped.append(cid)

    monkeypatch.setattr(mgr, "_path_readers", _no_readers)
    monkeypatch.setattr(mgr, "stop", _stop)
    await signals.touch_viewer("cam/z")
    await mgr._reap_once()
    assert stopped == []
    signals.init(_R())                                   # tomoshabin signali yo'q
    pub.last_request = time.monotonic() - 10_000
    await mgr._reap_once()
    assert stopped == ["z"]


async def test_choose_raw_uses_media_keyframe_signal(monkeypatch):
    from vizora.core.config import settings
    from vizora.media import ws_stream
    from vizora.services import camera_streams as cs

    monkeypatch.setattr(settings, "ws_stream_enabled", True)
    monkeypatch.setattr(settings, "ezviz_raw_path", True)
    monkeypatch.setattr(ws_stream, "has_keyframe", lambda p: False)   # hub bu jarayonda emas
    monkeypatch.setattr(mgr, "raw_only_running", lambda cid: False)
    cam = SimpleNamespace(id="c1", stream_path="cam/c1", is_ezviz=True, is_isup=False,
                          ezviz_drop_corrupt=False, ezviz_use_substream=False)
    if not cs.raw_eligible(cam, transport="ws", hevc=True):
        pytest.skip("raw_eligible boshqa maydonlarni talab qiladi")
    assert not await cs.choose_raw(cam, transport="ws", hevc=True)
    await signals.hub_keyframe("cam/c1/raw", True)
    assert await cs.choose_raw(cam, transport="ws", hevc=True)
