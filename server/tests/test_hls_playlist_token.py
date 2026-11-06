"""_inject_playlist_token: media playlist URI'lariga token qo'shilishi.

Cross-origin integratorlar uchun segment/part/init cookie'ga tayanmasin —
token URL'da bo'lsin. Bu funksiya sof (I/O yo'q), izolyatsiyada sinaladi.
"""
from vizora.api.media.hls import _inject_playlist_token


def _lines(body: bytes) -> list[str]:
    return body.decode().split("\n")


def test_bare_segment_line_gets_token():
    pl = b"#EXTINF:1.0,\nb1d7c86a_video1_seg7.mp4?session=abc\n"
    out = _lines(_inject_playlist_token(pl, "TOK"))
    assert "b1d7c86a_video1_seg7.mp4?session=abc&token=TOK" in out


def test_init_map_uri_gets_token():
    # Aynan foydalanuvchi ko'rgan holat: EXT-X-MAP init segmenti.
    pl = b'#EXT-X-MAP:URI="b1d7c86a_video1_init.mp4?session=abc"\n'
    out = _inject_playlist_token(pl, "TOK").decode()
    assert 'URI="b1d7c86a_video1_init.mp4?session=abc&token=TOK"' in out


def test_part_and_preload_hint_uri_get_token():
    pl = (b'#EXT-X-PART:DURATION=0.5,URI="seg_part3.mp4?session=abc"\n'
          b'#EXT-X-PRELOAD-HINT:TYPE=PART,URI="seg_part4.mp4?session=abc"\n')
    out = _inject_playlist_token(pl, "TOK").decode()
    assert 'URI="seg_part3.mp4?session=abc&token=TOK"' in out
    assert 'URI="seg_part4.mp4?session=abc&token=TOK"' in out


def test_uri_without_query_uses_question_mark():
    pl = b"segNoQuery.mp4\n"
    out = _inject_playlist_token(pl, "TOK").decode()
    assert "segNoQuery.mp4?token=TOK" in out


def test_existing_token_not_doubled():
    pl = b"seg.mp4?session=abc&token=OLD\n"
    out = _inject_playlist_token(pl, "NEW").decode()
    assert out.count("token=") == 1
    assert "token=OLD" in out


def test_tags_and_blank_lines_untouched():
    pl = b"#EXTM3U\n#EXT-X-VERSION:9\n\n#EXT-X-TARGETDURATION:1\n"
    out = _inject_playlist_token(pl, "TOK").decode()
    assert "#EXTM3U" in out
    assert "token=" not in out  # URI yo'q t'eglar tegilmaydi