"""Sub-stream MUSTAQIL transkod bayrog'i bilan sozlanishi.

Asosiy kafolat: kameraning ikkinchi profili (sub-stream) deyarli doim H.264 —
uni asosiy oqim HEVC bo'lgani UCHUN transkod qilish bekorga ikki barobar CPU/GPU
yeydi. `apply_rtsp_paths` sub path'ga `sub_transcode`ni beradi, `transcode`ni EMAS.
"""
from types import SimpleNamespace

import pytest

from vizora.media import mediamtx

_PATH = "cam/11111111-1111-1111-1111-111111111111"


def _cam(*, transcode=False, sub_transcode=False, sub=True):
    return SimpleNamespace(
        stream_path=_PATH,
        rtsp_url_enc=b"main",
        rtsp_sub_url_enc=b"sub" if sub else None,
        transcode=transcode,
        sub_transcode=sub_transcode,
    )


@pytest.fixture
def spy(monkeypatch):
    """(upserts, removed) — MediaMTX chaqiruvlarini yozib boradi."""
    upserts: list[tuple[str, str, bool]] = []
    removed: list[str] = []

    async def _upsert(name, rtsp_url, transcode):
        upserts.append((name, rtsp_url, transcode))

    async def _remove(name):
        removed.append(name)

    monkeypatch.setattr(mediamtx, "safe_upsert", _upsert)
    monkeypatch.setattr(mediamtx, "remove_path", _remove)
    # crypto.decrypt — shifrlangan baytni o'qiladigan matnga aylantiramiz.
    from vizora.core import crypto

    monkeypatch.setattr(crypto, "decrypt", lambda b: b.decode())
    return upserts, removed


@pytest.mark.asyncio
async def test_hevc_main_does_not_transcode_h264_sub(spy):
    """ASOSIY MUAMMO: asosiysi HEVC (transcode=True) bo'lsa ham sub TEGILMAYDI."""
    upserts, _ = spy
    await mediamtx.apply_rtsp_paths(_cam(transcode=True, sub_transcode=False))

    assert upserts == [
        (_PATH, "main", True),          # asosiy — HEVC, transkod kerak
        (f"{_PATH}/sub", "sub", False),  # sub — H.264, TO'G'RIDAN tortiladi
    ]


@pytest.mark.asyncio
async def test_hevc_sub_is_transcoded(spy):
    """Sub'ning O'ZI HEVC bo'lsa (probe aniqlagan) — u ham transkod qilinadi."""
    upserts, _ = spy
    await mediamtx.apply_rtsp_paths(_cam(transcode=True, sub_transcode=True))

    assert upserts[1] == (f"{_PATH}/sub", "sub", True)


@pytest.mark.asyncio
async def test_no_sub_url_leaves_path_alone_by_default(spy):
    """Tomoshabin yo'lida (prune_sub=False) ortiqcha DELETE urilmaydi."""
    upserts, removed = spy
    await mediamtx.apply_rtsp_paths(_cam(sub=False))

    assert upserts == [(_PATH, "main", False)]
    assert removed == []


@pytest.mark.asyncio
async def test_prune_sub_removes_stale_path(spy):
    """Admin CRUD'da (prune_sub=True) sub URL o'chirilsa path ham olib tashlanadi."""
    _, removed = spy
    await mediamtx.apply_rtsp_paths(_cam(sub=False), prune_sub=True)

    assert removed == [f"{_PATH}/sub"]


# --- RTSP manba transporti ---

def test_rtsp_source_forces_transport(monkeypatch):
    """`rtspTransport` BERILISHI shart.

    Berilmasa MediaMTX `automatic` ishlatadi: avval UDP'ni sinaydi, paket kelmasa
    TCP'ga o'tadi ("no UDP packets received, switching to TCP") va bu ~3s BEKOR
    kutish — har RTSP kamera ochilishiga qo'shiladi. O'lchandi: 5.54s -> 2.47s."""
    from vizora.core.config import settings as st
    from vizora.media import mediamtx
    cfg = mediamtx.build_path_config("rtsp://cam/1", transcode=False)
    assert cfg["rtspTransport"] == st.mediamtx_rtsp_transport
    assert st.mediamtx_rtsp_transport == "tcp", "standart TCP bo'lishi kerak"
    monkeypatch.setattr(st, "mediamtx_rtsp_transport", "automatic")
    assert mediamtx.build_path_config("rtsp://cam/1", transcode=False)[
        "rtspTransport"] == "automatic"


def test_transcode_path_has_no_source_key():
    """Transkod yo'li `runOnDemand` bilan ishlaydi — `source`/`rtspTransport` kerak emas."""
    from vizora.media import mediamtx
    cfg = mediamtx.build_path_config("rtsp://cam/1", transcode=True)
    assert "runOnDemand" in cfg and "source" not in cfg
