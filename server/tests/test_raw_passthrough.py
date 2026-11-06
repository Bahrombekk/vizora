"""Transkodsiz `<path>/raw` chiqishi — panel pleyeri uchun eng tez yo'l.

O'lchandi (simli Hik, sovuq ochilish 2.41s): ffmpeg/NVENC init -> MediaMTX ready
0.97s + birinchi kalit kadrni kutish 0.44s. `-c:v copy` bu ikkisini ham yo'q
qiladi. Asosiy path H.264 bo'lib qolishi SHART — HLS/WebRTC va tashqi
iste'molchilar o'sha yerdan o'qiydi.
"""
from vizora.sources.ezviz import manager
from vizora.sources.ezviz.publish import _ffmpeg_cmd, _ffmpeg_copy_cmd, _raw_output

HD = "rtsp://mtx:8554/cam/x"
SD = "rtsp://mtx:8554/cam/x/sub"
RAW = "rtsp://mtx:8554/cam/x/raw"


def _outputs(cmd):
    """Har bir `-f rtsp <url>` chiqishi uchun (url, undan oldingi kodek) juftligi."""
    out = []
    for i, a in enumerate(cmd):
        if a == "-f" and cmd[i + 1] == "rtsp":
            # `-f rtsp [-rtsp_transport tcp] <url>`
            j = i + 2
            while cmd[j].startswith("-"):
                j += 2
            url = cmd[j]
            codec = None
            for j in range(i, 0, -1):
                if cmd[j] == "-c:v":
                    codec = cmd[j + 1]
                    break
            out.append((url, codec))
    return out


def test_raw_output_is_copy_and_first():
    cmd = _ffmpeg_cmd("hevc", HD, SD, RAW)
    outs = _outputs(cmd)
    assert outs[0] == (RAW, "copy"), f"xom chiqish birinchi va copy bo'lishi kerak: {outs}"
    # Asosiy va SD hali ham QAYTA KODLANADI (iste'molchilar H.264 kutadi)
    assert [u for u, _ in outs] == [RAW, HD, SD]
    assert all(c != "copy" for u, c in outs if u != RAW)


def test_raw_maps_original_input_not_filtered():
    """Xom chiqish filtrdan EMAS, asl kirishdan olinishi kerak (`-map 0:v`) —
    aks holda u ham dekod/scale qilinadi va butun foyda yo'qoladi."""
    cmd = _ffmpeg_cmd("hevc", HD, SD, RAW)
    i = cmd.index(RAW)
    seg = cmd[:i]
    assert "0:v" in seg[seg.index("-map"):seg.index("-map") + 2]


def test_copy_mode_also_publishes_raw():
    """Manba H.264 bo'lsa ham `/raw` bo'lishi kerak — pleyer DOIM shu nomni
    so'raydi, path yo'q bo'lsa 45s kutib qolardi."""
    outs = _outputs(_ffmpeg_copy_cmd("h264", HD, SD, RAW))
    assert [u for u, _ in outs] == [RAW, HD, SD]
    assert all(c == "copy" for _, c in outs)


def test_raw_disabled_keeps_old_command():
    for cmd in (_ffmpeg_cmd("hevc", HD, SD, None), _ffmpeg_copy_cmd("h264", HD, SD, None)):
        assert [u for u, _ in _outputs(cmd)] == [HD, SD]
    assert _raw_output(None) == []


def test_reap_counts_readers_on_every_subpath():
    """WS pleyeri `/raw` dan o'qiydi — faqat asosiy path sanalsa, ko'rilayotgan
    kamera "idle" deb o'ldirilardi."""
    r = {"cam/x": 0, "cam/x/raw": 1, "cam/x/sub": 0, "cam/y": 5}
    assert manager._readers_for(r, "cam/x") == 1
    assert manager._readers_for(r, "cam/y") == 5
    assert manager._readers_for({}, "cam/x") == 0
    # Boshqa kameraning nomi prefiks bo'lib qolmasin
    assert manager._readers_for({"cam/xyz": 3}, "cam/x") == 0
def _cam(**kw):
    import uuid
    from types import SimpleNamespace
    cid = uuid.uuid4()
    base = dict(id=cid, stream_path=f"cam/{cid}", is_ezviz=True, is_isup=False,
                has_sub=False, ezviz_drop_corrupt=False, status="online")
    base.update(kw)
    return SimpleNamespace(**base)


def test_ws_url_points_to_raw_only_when_client_takes_hevc(monkeypatch):
    """XOM rejimda publisher FAQAT `/raw` ga yozadi — asosiy path tayyor bo'lmaydi.
    Shuning uchun ws_url ham o'sha path'ga ishora qilishi SHART, aks holda pleyer
    hech qachon kelmaydigan oqimni 45s kutadi."""
    from vizora.core.config import settings
    from vizora.services import camera_streams as cs

    monkeypatch.setattr(settings, "ws_stream_enabled", True)
    monkeypatch.setattr(settings, "ezviz_raw_path", True)
    cam = _cam()
    # Har bir shart majburiy: WS + HEVC + EZVIZ + xom yo'l yoqilgan + drop_corrupt emas
    assert cs.raw_eligible(cam, transport="ws", hevc=True)
    assert not cs.raw_eligible(cam, transport="ws", hevc=False)
    assert not cs.raw_eligible(cam, transport="hls", hevc=True)
    assert not cs.raw_eligible(cam, transport="webrtc", hevc=True)
    assert not cs.raw_eligible(_cam(is_ezviz=False), transport="ws", hevc=True)
    assert not cs.raw_eligible(_cam(ezviz_drop_corrupt=True), transport="ws", hevc=True)
    monkeypatch.setattr(settings, "ezviz_raw_path", False)
    assert not cs.raw_eligible(cam, transport="ws", hevc=True)

    raw = cs.ws_links(cam, "T", raw=True, substream=False)
    assert raw["ws_url"].startswith(f"/ws/stream/{cam.stream_path}/raw") and raw["ws_raw"] is True
    assert "ws_sub_url" not in raw
    plain = cs.ws_links(_cam(has_sub=True), "T", raw=False, substream=False)
    assert "/raw" not in plain["ws_url"] and plain["ws_raw"] is False and "ws_sub_url" in plain


def test_panel_stream_needs_h264_only_without_raw():
    """Panel /stream: xom yo'l tanlanmasa publisher H.264 enkoderi bilan ishlaydi."""
    import inspect
    from vizora.api.panel import cameras
    src = inspect.getsource(cameras.get_camera_stream)
    assert "hevc: bool = Query" in src, "brauzer imkoniyati /stream ga uzatilmaydi"
    assert "choose_raw(" in src
    assert "need_h264=not raw" in src


def test_raw_only_command_has_no_encoder():
    """XOM rejim buyrug'ida enkoder ham, filtr ham bo'lmasligi kerak."""
    from vizora.sources.ezviz.publish import _ffmpeg_raw_only_cmd
    cmd = _ffmpeg_raw_only_cmd("hevc", RAW)
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-filter_complex" not in cmd
    assert not any(a in ("h264_nvenc", "libx264") for a in cmd)
    assert [u for u, _ in _outputs(cmd)] == [RAW]


def test_raw_only_never_used_for_drop_corrupt():
    """drop_corrupt kamerada oqim PyAV bilan DEKODLANADI — passthrough imkonsiz."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "vizora" / "sources" / "ezviz" / "manager.py").read_text(encoding="utf-8")
    rows = [ln for ln in src.splitlines() if "raw_only = (not need_h264)" in ln]
    assert rows, "raw_only hisobi topilmadi"
    blok = src[src.index(rows[0]):src.index(rows[0]) + 300]
    assert "drop_corrupt" in blok


def test_publisher_upgrades_mode_when_h264_needed():
    """Panel XOM rejimda ochgan bo'lsa-u, keyin HLS/iste'molchi kelsa — enkoder
    qo'shishning yo'li yo'q, publisher QAYTA boshlanishi kerak."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "vizora" / "sources" / "ezviz" / "manager.py").read_text(encoding="utf-8")
    assert "if pub and pub.alive and pub.raw_only and need_h264:" in src
    assert "raw_only" in src.split("__slots__")[1][:120], "_Publisher.raw_only saqlanmaydi"


def test_probe_counts_subpaths_as_camera_ready():
    """XOM rejimda publisher FAQAT `<path>/raw` ga yozadi — asosiy path tayyor
    bo'lmaydi. Probe faqat asosiy nomni qaraganda oqib turgan kamera "oflayn"
    bo'lib ko'rinardi (devorda plitkalar yo'qolib qolardi)."""
    from vizora.services.probe import _base_path
    assert _base_path("cam/abc/raw") == "cam/abc"
    assert _base_path("cam/abc/sub") == "cam/abc"
    assert _base_path("cam/abc") == "cam/abc"


async def test_raw_only_when_it_opens_fast(monkeypatch):
    """Xom yo'lda brauzer kameraning O'Z I-kadrini kutadi (past fps NVR'da 12-29s
    o'lchangan). Shuning uchun u faqat darhol ochiladigan holatda beriladi; sovuq
    ochilish H.264 (enkoder har 1s da I-kadr)."""
    from vizora.core.config import settings
    from vizora.media import ws_stream
    from vizora.services import camera_streams as cs
    from vizora.sources.ezviz import manager as mgr

    monkeypatch.setattr(settings, "ws_stream_enabled", True)
    monkeypatch.setattr(settings, "ezviz_raw_path", True)
    warm_hubs, raw_pubs = set(), set()
    monkeypatch.setattr(ws_stream, "has_keyframe", lambda p: p in warm_hubs)
    monkeypatch.setattr(mgr, "raw_only_running", lambda cid: cid in raw_pubs)
    cam = _cam()
    kw = dict(transport="ws", hevc=True)

    assert not await cs.choose_raw(cam, **kw)                       # sovuq -> H.264
    assert await cs.choose_raw(cam, substream=True, **kw)           # devor: doim xom (CPU)
    warm_hubs.add(f"{cam.stream_path}/raw")
    assert await cs.choose_raw(cam, **kw)                           # hub keshida I-kadr bor
    warm_hubs.clear()
    raw_pubs.add(str(cam.id))
    assert await cs.choose_raw(cam, **kw)                           # xom publisher — qayta boshlatmaymiz
    assert not await cs.choose_raw(cam, transport="ws", hevc=False) # brauzer HEVC bilmaydi


def test_ws_hub_keeps_gop_long_enough():
    """Hub 3s da yopilsa GOP-kesh yo'qoladi va qayta ochish yana I-kadrni kutadi."""
    from vizora.core.config import Settings
    assert Settings.model_fields["ws_stream_idle_seconds"].default >= 30


def test_transcoding_publisher_reaped_sooner(monkeypatch):
    """Enkoderli (to'liq rejim) publisher tomoshabinsiz Hik'ning 900s muddatini
    kutmaydi — aks holda har ochilgan kamera ~1 yadroni 15 daqiqa band qiladi."""
    import asyncio
    import time
    from types import SimpleNamespace
    from vizora.core.config import settings
    from vizora.sources.ezviz import manager as mgr

    monkeypatch.setattr(settings, "hik_idle_seconds", 900)
    monkeypatch.setattr(settings, "ezviz_transcode_idle_seconds", 60)
    monkeypatch.setattr(settings, "always_warm_camera_ids", "")
    stopped = []

    async def _readers():
        return {}

    async def _stop(cid):
        stopped.append(cid)

    monkeypatch.setattr(mgr, "_path_readers", _readers)
    monkeypatch.setattr(mgr, "stop", _stop)
    old = time.monotonic() - 120
    proc = SimpleNamespace(returncode=None)
    pubs = {
        "full": SimpleNamespace(proc=proc, alive=True, stream_path="cam/full", provider="hik",
                                raw_only=False, last_request=old),
        "raw": SimpleNamespace(proc=proc, alive=True, stream_path="cam/raw", provider="hik",
                               raw_only=True, last_request=old),
    }
    monkeypatch.setattr(mgr, "_publishers", pubs)
    asyncio.run(mgr._reap_once())
    assert stopped == ["full"]
