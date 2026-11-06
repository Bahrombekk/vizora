"""FastAPI ilovasini yig'ish: middleware, router'lar, SPA.

Ishga tushish/to'xtash mantig'i va fon vazifalari — `vizora.lifespan`."""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from vizora.core.config import settings

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
# Web "Loglar" bo'limi uchun xotira buferi (oxirgi loglarni SSH'siz ko'rish).
# Router'lardan OLDIN o'rnatiladi — ular import paytida ham log yozishi mumkin.
from vizora.core import logbuf  # noqa: E402

logbuf.install()
log = logging.getLogger("vizora")

from vizora.api.panel import (  # noqa: E402
    auth, cameras, dashboard, branches, ezviz_accounts, zones, isup, monitoring,
)
from vizora.api.integration import rest as consumer_rest  # noqa: E402
from vizora.api.integration import ws as consumer_ws  # noqa: E402
from vizora.api.deps import origin_allowed  # noqa: E402
from vizora.api.media import hls, whep  # noqa: E402
from vizora.api.media import ws as media_ws  # noqa: E402
from vizora.core.db import engine  # noqa: E402
from vizora.lifespan import lifespan  # noqa: E402

app = FastAPI(
    title="Vizora",
    description="Filial/hudud kameralarini markazlashtirish va oqim ulashish",
    version="0.1.0",
    lifespan=lifespan,
    # Production'da API sxemasi ochiq turmasin (hujumchiga tayyor xarita bo'lardi).
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)

_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_origin_guard(request, call_next):
    """Holat o'zgartiruvchi panel so'rovi begona sahifadan kelmasin (CSRF).

    SameSite=Lax cookie boshqa saytdan yuborilmaydi, lekin qo'shni subdomen "same-site"
    hisoblanadi. Brauzer bunday so'rovda doim `Origin` yuboradi — u panelning o'z
    manzili bo'lmasa rad etamiz. Tashqi API (/api/v1, X-API-Key) bunga kirmaydi."""
    p = request.url.path
    if (request.method in _UNSAFE and p.startswith("/api/") and not p.startswith("/api/v1/")
            and not origin_allowed(request.headers)):
        log.warning("CSRF: begona Origin rad etildi: %s %s (Origin=%s)",
                    request.method, p, request.headers.get("origin"))
        return JSONResponse({"detail": "Ruxsat etilmagan manba (Origin)"}, status_code=403)
    return await call_next(request)


# Oqim yo'llari (/hls, /whep) tashqi integratorlar uchun ochiq CORS'ga muhtoj —
# ular token bilan himoyalangan, shuning uchun origin xavfsizlik chegarasi emas.
# CORSMiddleware va CSRF qo'riqchisidan KEYIN qo'shiladi -> eng TASHQI qatlam:
# preflight'ni global CORS 400 bermasidan ushlaydi va javobga ACAO:* qo'shadi.
# `/api` esa global CORSMiddleware bilan qat'iy allowlist'da qoladi.
# Batafsil: api/media/stream_cors.py
from vizora.api.media.stream_cors import StreamCORSMiddleware  # noqa: E402

app.add_middleware(StreamCORSMiddleware)


@app.get("/health", tags=["system"])
async def health() -> dict:
    """Sog'liq tekshiruvi — DB va Redis ulanishini sinaydi."""
    db_ok = False
    redis_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("DB health xato: %s", exc)
    try:
        redis_ok = bool(await app.state.redis.ping())
    except Exception as exc:  # noqa: BLE001
        log.warning("Redis health xato: %s", exc)

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "version": app.version,
        "checks": {"database": db_ok, "redis": redis_ok},
    }


# Router'lar rolga qarab (`APP_ROLE`, [[config.app_role]]): `media` jarayoni faqat
# video endpoint'larini, `api` esa qolganini ko'taradi; `all` — ikkalasini.
if settings.runs_api:
    # --- Admin panel REST: /api/* ---
    for _r in (auth, branches, zones, ezviz_accounts, isup, cameras, dashboard, monitoring):
        app.include_router(_r.router, prefix="/api")
    # --- Tashqi tizimlar: /api/v1/* (REST) va /ws/consumer ---
    app.include_router(consumer_rest.router, prefix="/api")
    app.include_router(consumer_ws.router)
if settings.runs_media:
    # --- Oqim protokollari: /hls, /whep, /ws/stream ---
    app.include_router(hls.router)
    app.include_router(whep.router)
    app.include_router(media_ws.router)


# --- Frontend SPA (production: backend dist xizmat qiladi) ---
_dist = settings.frontend_dist
if settings.runs_api and os.path.isdir(_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_dist, "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        index = os.path.join(_dist, "index.html")
        return FileResponse(index)
