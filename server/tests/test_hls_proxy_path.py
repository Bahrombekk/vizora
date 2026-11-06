"""HLS proxy yo'l tahlili — path-traversal / cross-camera bypass'ga qarshi.

`_parse_hls_path` upstream URL'ni FAQAT tekshirilgan qismlardan quradi. Bu test
boshqa kameraga sakrash (`..`) va yaroqsiz shakllar rad etilishini kafolatlaydi.
"""
from vizora.api.media.hls import _parse_hls_path

_A = "11111111-1111-1111-1111-111111111111"
_B = "22222222-2222-2222-2222-222222222222"


def test_valid_index():
    cam, mtx = _parse_hls_path(f"cam/{_A}/index.m3u8")
    assert cam == f"cam/{_A}"
    assert mtx == f"cam/{_A}/index.m3u8"


def test_valid_sub_and_segment():
    cam, mtx = _parse_hls_path(f"cam/{_A}/sub/index.m3u8")
    assert cam == f"cam/{_A}"          # auth hali ham asosiy kameraga bog'langan
    assert mtx == f"cam/{_A}/sub/index.m3u8"
    _, seg = _parse_hls_path(f"cam/{_A}/seg7.mp4")
    assert seg == f"cam/{_A}/seg7.mp4"


def test_traversal_to_other_camera_blocked():
    # Eski regex bunda cam=A qaytarib, A tokeni bilan B'ni ko'rsatardi.
    assert _parse_hls_path(f"cam/{_A}/../../cam/{_B}/index.m3u8") is None
    assert _parse_hls_path(f"cam/{_A}/../{_B}/index.m3u8") is None
    assert _parse_hls_path(f"cam/{_A}/..") is None
    assert _parse_hls_path(f"cam/{_A}/sub/../../cam/{_B}/index.m3u8") is None


def test_non_cam_and_malformed_rejected():
    assert _parse_hls_path("etc/passwd") is None
    assert _parse_hls_path(f"cam/{_A}") is None              # fayl segmenti yo'q
    assert _parse_hls_path("cam/not-a-uuid/index.m3u8") is None
    assert _parse_hls_path(f"cam/{_A}/deep/extra/index.m3u8") is None
    assert _parse_hls_path(f"cam/{_A}extrahex/index.m3u8") is None  # greedy prefix yo'q
