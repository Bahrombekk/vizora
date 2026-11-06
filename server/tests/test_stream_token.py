"""Oqim tokeni — kameraga bog'langan, qisqa muddatli JWT."""
from vizora.media import stream


def test_valid_token():
    token, exp = stream.make_stream_token("cam/abc")
    assert stream.verify_stream_token(token)
    assert stream.verify_stream_token(token, "cam/abc")
    assert isinstance(exp, str)


def test_wrong_camera_rejected():
    token, _ = stream.make_stream_token("cam/abc")
    assert not stream.verify_stream_token(token, "cam/other")


def test_garbage_rejected():
    assert not stream.verify_stream_token("not-a-jwt")
    assert not stream.verify_stream_token("")


def test_stream_url_format():
    assert stream.stream_url("cam/abc", "TOK") == "/hls/cam/abc/index.m3u8?token=TOK"
