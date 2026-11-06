"""StreamCORSMiddleware: oqim yo'llariga ochiq CORS, /api'ga tegmaydi.

To'liq app'ni (DB/Redis) ko'tarmasdan, middleware'ni kichik soxta ASGI app bilan
o'rab, to'g'ridan-to'g'ri ASGI xabarlari bilan sinaymiz.
"""
import asyncio

from vizora.api.media.stream_cors import StreamCORSMiddleware


async def _dummy_app(scope, receive, send):
    """Har doim 401 (token'siz oqim javobiga o'xshash) qaytaradigan soxta app."""
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({"type": "http.response.body", "body": b"no"})


def _run(path: str, method: str = "GET"):
    app = StreamCORSMiddleware(_dummy_app)
    scope = {"type": "http", "method": method, "path": path, "headers": []}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.lower(): v for k, v in start["headers"]}
    return start["status"], headers


def test_hls_get_gets_wildcard_cors():
    status, headers = _run("/hls/cam/abc/index.m3u8")
    assert status == 401  # auth'dan mustaqil — CORS baribir qo'shiladi
    assert headers.get(b"access-control-allow-origin") == b"*"
    # kredensial YO'Q: ACAO:* bilan birga bo'lsa kredensialli so'rovni buzardi
    assert b"access-control-allow-credentials" not in headers


def test_whep_get_gets_wildcard_cors():
    _, headers = _run("/whep/cam/abc/whep")
    assert headers.get(b"access-control-allow-origin") == b"*"


def test_hls_options_preflight_short_circuits():
    status, headers = _run("/hls/cam/abc/index.m3u8", method="OPTIONS")
    assert status == 204
    assert headers.get(b"access-control-allow-origin") == b"*"
    assert b"GET" in headers.get(b"access-control-allow-methods", b"")


def test_api_path_untouched():
    # /api oqim yo'li EMAS — bu middleware unga CORS qo'shmaydi (global
    # CORSMiddleware qat'iy allowlist bilan boshqaradi).
    _, headers = _run("/api/cameras")
    assert b"access-control-allow-origin" not in headers


def test_existing_acao_is_deduped():
    """Ichki qatlam ACAO qo'ygan bo'lsa ham, natijada bittagina ACAO:* bo'ladi."""
    async def app_with_acao(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"access-control-allow-origin", b"http://localhost:5173")],
        })
        await send({"type": "http.response.body", "body": b"ok"})

    mw = StreamCORSMiddleware(app_with_acao)
    scope = {"type": "http", "method": "GET", "path": "/hls/x", "headers": []}
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    acao = [v for k, v in start["headers"] if k.lower() == b"access-control-allow-origin"]
    assert acao == [b"*"]


def test_whep_preflight_allows_post_and_exposes_location():
    """Tashqi integrator WHEP'ni ishlata olsin: POST/PATCH/DELETE preflight'dan o'tadi,
    sessiya manzili (Location) JS'ga ko'rinadi (audit 2026-11-03)."""
    status, headers = _run("/whep/cam/abc/whep", method="OPTIONS")
    methods = headers.get(b"access-control-allow-methods", b"")
    assert status == 204 and all(m in methods for m in (b"POST", b"PATCH", b"DELETE"))
    assert b"access-control-allow-credentials" not in headers
    _, headers = _run("/whep/cam/abc/whep")
    assert b"Location" in headers.get(b"access-control-expose-headers", b"")
