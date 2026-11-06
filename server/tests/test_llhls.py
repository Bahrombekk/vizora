"""LL-HLS (Low-Latency HLS) qo'llab-quvvatlashi.

Nega: mpegts variantida pleyer 2-3 TO'LIQ segment (~3-4s) buferlashi shart edi.
LL-HLS segment ichidagi 200ms "part"larni uzatadi — ochilish ~4s dan ~0.6s ga
tushdi va kechikish ~1s bo'ldi. WebRTC'dan farqli: oddiy HTTP, port kerak emas.
"""
import pytest

from vizora.api.media import hls as hls_proxy
from vizora.sources.ezviz.manager import _playable_count


# --- Tayyorlik hisobi: GAP aldamasin ---

LL_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:10
#EXT-X-TARGETDURATION:1
#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES,PART-HOLD-BACK=0.50000
#EXT-X-PART-INF:PART-TARGET=0.20000
#EXT-X-MEDIA-SEQUENCE:1
#EXT-X-MAP:URI="abc_video1_init.mp4"
#EXT-X-GAP
#EXTINF:1.00000,
gap.mp4
#EXT-X-GAP
#EXTINF:1.00000,
gap.mp4
#EXT-X-PROGRAM-DATE-TIME:2026-08-21T11:24:19.278Z
#EXT-X-PART:DURATION=0.20000,URI="abc_video1_part0.mp4",INDEPENDENT=YES
#EXT-X-PART:DURATION=0.20000,URI="abc_video1_part1.mp4"
#EXT-X-PART:DURATION=0.20000,URI="abc_video1_part2.mp4"
"""

MPEGTS_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:1.00000,
abc_main_seg0.ts
#EXTINF:1.00000,
abc_main_seg1.ts
"""

GAPS_ONLY = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-GAP
#EXTINF:1.00000,
gap.mp4
#EXT-X-GAP
#EXTINF:1.00000,
gap.mp4
"""


def test_ll_playlist_counts_parts_not_gaps():
    """LL-HLS'da haqiqiy signal — `#EXT-X-PART`, `#EXTINF` emas.

    MediaMTX playlistni GAP'lar bilan to'ldiradi: oddiy EXTINF sanog'i oqim
    tayyor bo'lmasa ham 'tayyor' deb ko'rsatardi."""
    n, ll = _playable_count(LL_PLAYLIST)
    assert ll is True
    assert n == 3, "part'lar sanalishi kerak (GAP emas)"


def test_mpegts_playlist_counts_real_segments():
    n, ll = _playable_count(MPEGTS_PLAYLIST)
    assert ll is False
    assert n == 2


def test_gap_only_playlist_is_not_playable():
    """Faqat GAP bo'lsa oqim TAYYOR EMAS — 0 qaytishi kerak."""
    n, ll = _playable_count(GAPS_ONLY)
    assert ll is False
    assert n == 0, "GAP'lar o'ynatiladigan bo'lak sifatida sanalmasligi kerak"


# --- Variant nomi qattiq yozilmasligi ---

def test_master_playlist_uses_discovered_variant_names():
    """Media playlist nomi variantga bog'liq (mpegts: main_stream.m3u8,
    lowLatency: video1_stream.m3u8) — qattiq yozilsa LL-HLS'da 404 bo'lardi."""
    out = hls_proxy._master_playlist("TOK", "video1_stream.m3u8", "video1_stream.m3u8")
    assert "video1_stream.m3u8?token=TOK" in out
    assert "sub/video1_stream.m3u8?token=TOK" in out
    assert "main_stream" not in out


def test_master_playlist_still_works_for_mpegts():
    out = hls_proxy._master_playlist("TOK", "main_stream.m3u8", "main_stream.m3u8")
    assert "main_stream.m3u8?token=TOK" in out
    assert "sub/main_stream.m3u8?token=TOK" in out


def test_variant_regex_ignores_session_query():
    """index.m3u8'dagi variant qatori `?session=...` bilan keladi — faqat fayl
    nomi olinishi kerak."""
    body = ('#EXTM3U\n#EXT-X-VERSION:10\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=3508320\n'
            'video1_stream.m3u8?session=ac4408f6-8729-4e80-b930-9694b7b8d2f2\n')
    m = hls_proxy._VARIANT_RE.search(body)
    assert m and m.group(1) == "video1_stream.m3u8"


# --- Blocking playlist parametrlari ---

def test_blocking_params_are_forwarded():
    """`_HLS_msn`/`_HLS_part` upstream'ga UZATILISHI shart — busiz MediaMTX
    playlistni darhol qaytaradi va past kechikish yo'qoladi. Sinovda MediaMTX
    to'g'ridan 0.21-0.75s blokladi, proxy orqali 0.48-1.09s."""
    assert "_HLS_msn" in hls_proxy._LL_PARAMS
    assert "_HLS_part" in hls_proxy._LL_PARAMS
    assert "_HLS_skip" in hls_proxy._LL_PARAMS


def test_only_ll_params_forwarded_not_token():
    """Mijoz query'sining qolgani (token) upstream'ga KETMASLIGI kerak."""
    import inspect
    src = inspect.getsource(hls_proxy.hls_proxy)
    assert "for k in _LL_PARAMS" in src
    # Butun query'ni uzatish belgisi bo'lmasin
    assert "params=request.query_params" not in src.replace(" ", "")


def test_fmp4_filenames_pass_path_check():
    """LL-HLS fMP4 fayllari (init.mp4, part0.mp4) yo'l tekshiruvidan o'tishi kerak."""
    cam = "cam/0f3a1c9e-2b7d-4c1a-9e8f-1234567890ab"
    for f in ("abc_video1_init.mp4", "abc_video1_part0.mp4",
              "abc_video1_seg7.mp4", "video1_stream.m3u8"):
        assert hls_proxy._parse_hls_path(f"{cam}/{f}") is not None, f
        assert hls_proxy._parse_hls_path(f"{cam}/sub/{f}") is not None, f
