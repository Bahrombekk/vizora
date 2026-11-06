"""Log redaction bo'shliqlari (audit 2026-11-03)."""
from vizora.core.logbuf import redact


def test_rtsp_password_with_slash():
    out = redact("ulanish rtsp://admin:pa/ss@192.168.1.5:554/Streaming")
    assert "pa/ss" not in out and "rtsp://***:***@192.168.1.5" in out


def test_json_session_id_and_password():
    msg = '{"meta": {"code": 200}, "loginSession": {"sessionId": "abc.def", "rfSessionId": "zz"}, "password": "x1"}'
    out = redact(msg)
    assert "abc.def" not in out and "zz" not in out and "x1" not in out
    assert '"code": 200' in out


def test_query_and_basic_auth():
    out = redact("GET /hls/x?session_id=s3cr3t&token=t0k Authorization: Basic Y3NhcHA6cHc=")
    assert "s3cr3t" not in out and "t0k" not in out and "Y3NhcHA6cHc=" not in out
