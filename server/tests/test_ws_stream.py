"""WS xom oqim: path regex, kadr sarlavhasi, obunachi backpressure, GOP burst."""
import asyncio
import struct

import pytest

from vizora.media import rtsp_reader as rr
from vizora.media import ws_stream
from vizora.api.media.ws import parse_path

CID = "8b1e2c3d-4f50-4a61-b2c3-d4e5f6a7b8c9"


def test_parse_path_accepts_main_and_sub_only():
    assert parse_path(f"cam/{CID}") == (f"cam/{CID}", f"cam/{CID}")
    assert parse_path(f"cam/{CID.upper()}/sub") == (f"cam/{CID}", f"cam/{CID}/sub")
    for bad in ("cam/abc", f"cam/{CID}/x", f"cam/{CID}/sub/whep", f"../cam/{CID}", f"cam/{CID}/", ""):
        assert parse_path(bad) is None, bad


def test_pack_frame_header_layout():
    fr = ws_stream.pack_frame(True, 1_234_567, b"\x00\x00\x00\x01\x65")
    assert len(fr) == 12 + 5
    flags, ts = struct.unpack(">B3xQ", fr[:12])
    assert flags == 1 and ts == 1_234_567 and fr[12:] == b"\x00\x00\x00\x01\x65"
    assert ws_stream.pack_frame(False, -5, b"")[0] == 0   # manfiy ts -> 0 (uint64)


def test_subscriber_waits_for_keyframe_and_recovers_after_overflow():
    sub = ws_stream.Subscriber()
    sub.push(ws_stream.pack_frame(False, 0, b"p"), key=False)   # kalitdan oldin — tashlanadi
    assert sub.queue.empty()
    sub.push(ws_stream.pack_frame(True, 0, b"k"), key=True)
    sub.push(ws_stream.pack_frame(False, 1, b"p"), key=False)
    assert sub.queue.qsize() == 2
    # Navbat to'lsin
    for i in range(ws_stream._QUEUE_MAX + 5):
        sub.push(ws_stream.pack_frame(False, i, b"p"), key=False)
    assert sub.dropped == 1 and sub.need_key is True
    # To'lganda navbat bo'shatildi; keyingi P kadrlar tashlanadi, kalit kadr qabul qilinadi
    assert sub.queue.qsize() == 0
    sub.push(ws_stream.pack_frame(False, 9, b"p"), key=False)
    assert sub.queue.qsize() == 0
    sub.push(ws_stream.pack_frame(True, 10, b"k"), key=True)
    assert sub.queue.qsize() == 1 and sub.need_key is False


def test_subscriber_json_messages_always_pass():
    sub = ws_stream.Subscriber()
    sub.push({"type": "init"})
    assert sub.queue.qsize() == 1


@pytest.mark.asyncio
async def test_hub_gop_burst_for_late_subscriber(monkeypatch):
    """Kech qo'shilgan tomoshabin init + so'nggi GOP (kalitdan boshlab) oladi."""
    hub = ws_stream.Hub("cam/x")
    # RTSP task boshlanmasin — subscribe() ichidagi create_task'ni o'chiramiz
    monkeypatch.setattr(hub, "_run", lambda: asyncio.sleep(0))
    track = rr.Track(codec=rr.H264, payload_type=96, control="", sps=b"\x67\x64\x00\x1f", pps=b"\x68\xee")
    await hub._on_track(track)
    assert hub.init_msg["codecString"] == "avc1.64001F"

    # Kalitdan oldingi P kadr — hech kim dekodlay olmaydi, GOP'ga tushmaydi
    await hub._on_au(rr.AccessUnit(nals=[b"\x41\x01"], ts_us=0, key=False))
    assert hub.gop == []
    await hub._on_au(rr.AccessUnit(nals=[b"\x65\x01"], ts_us=1000, key=True))
    await hub._on_au(rr.AccessUnit(nals=[b"\x41\x02"], ts_us=2000, key=False))
    await hub._on_au(rr.AccessUnit(nals=[b"\x41\x03"], ts_us=3000, key=False))
    assert len(hub.gop) == 3
    # Kalit kadrga SPS/PPS qo'shilgan
    assert hub.gop[0][12:].startswith(b"\x00\x00\x00\x01\x67\x64\x00\x1f\x00\x00\x00\x01\x68\xee\x00\x00\x00\x01\x65")

    sub = hub.subscribe()
    assert sub.queue.qsize() == 4                     # init + 3 kadr
    assert sub.queue.get_nowait()["type"] == "init"
    first = sub.queue.get_nowait()
    assert first[0] & 1 == 1                          # birinchi — kalit
    # Yangi kalit kadr GOP'ni almashtiradi
    await hub._on_au(rr.AccessUnit(nals=[b"\x65\x09"], ts_us=4000, key=True))
    assert len(hub.gop) == 1
    hub.unsubscribe(sub)
    assert hub.idle_since is not None
    await hub.close()


def test_parse_path_accepts_raw_variant():
    """`/raw` — transkodsiz oqim (odatda HEVC). Token ASOSIY path'ga bog'langani
    uchun bazaviy nom o'zgarmasligi shart."""
    assert parse_path(f"cam/{CID}/raw") == (f"cam/{CID}", f"cam/{CID}/raw")
    assert parse_path(f"cam/{CID}/raw/x") is None
    assert parse_path(f"cam/{CID}/rawx") is None


def test_hub_base_path_is_main_even_for_raw():
    """Hub `/raw` dan o'qiyotganda ham publisher'ning ASOSIY nomini bilishi kerak —
    idle soatini shu nom bo'yicha yangilaydi (aks holda ko'rilayotgan kamera
    o'ldiriladi)."""
    assert ws_stream.Hub(f"cam/{CID}/raw").base_path == f"cam/{CID}"
    assert ws_stream.Hub(f"cam/{CID}/sub").base_path == f"cam/{CID}"
    assert ws_stream.Hub(f"cam/{CID}").base_path == f"cam/{CID}"
