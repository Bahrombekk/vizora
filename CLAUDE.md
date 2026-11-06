# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

**Vizora** — corporate video-surveillance platform for a private company. Aggregates
IP cameras (RTSP, EZVIZ / Hik-Connect cloud, Hikvision ISUP 5.0), tracks
online/offline status, plays live video in the browser (WS+WebCodecs → WHEP → HLS)
and re-shares streams to external systems via API key.
Stack: **FastAPI · async SQLAlchemy (asyncpg) · PostgreSQL · Redis · MediaMTX ·
React (Vite) · Docker**.

It was forked (2026-11-05) from an internal "central-server" project built for a
different organisation. The streaming engine is the same; the domain model,
naming, branding and UI were changed. Code comments, logs and user-facing strings
are in **Uzbek** — keep that convention.

## Layout

```
server/                 Python backend (package `vizora`)
  vizora/main.py         FastAPI assembly only
  vizora/lifespan.py     startup checks, default admin, MediaMTX sync, background jobs
  vizora/core/           config, db, security, crypto, logbuf — knows nothing about cameras
  vizora/models|schemas/ ORM + Pydantic
  vizora/api/            routers ONLY: panel/ (admin panel /api/*), integration/
                        (/api/v1/* REST + /ws/consumer), media/ (/hls /whep /ws/stream)
  vizora/services/       camera_streams (THE orchestrator), access, probe, stats, snapshot
  vizora/media/          MediaMTX client, stream tokens, WS hub, RTSP reader, ffmpeg
  vizora/sources/        ezviz/ (cloud publisher subprocess), isup.py
  alembic/versions/     0001_initial = squashed schema for this fork
  tests/
panel/                  React SPA (Vite)
  src/app/              App.jsx (routes), Layout.jsx (top masthead nav), ChangePassword
  src/lib/theme.jsx     ALL design tokens (light default + dark) → CSS variables
  src/styles.css        uses only tokens (`var(--x)`, `rgb(var(--accentRgb) / a)`)
  src/player/           Player.jsx, wsplayer.js — all camera video code
  src/pages/            one file per route
isup/                   C++ ISUP bridge (SDK not in git — see isup/README.md)
```

Dependencies point downward only: `api → services → sources/media → core`.
`services/camera_streams.py` is the ONLY place that decides how a camera source is
started; routers never branch on source type themselves.

## Domain model

`Branch` (Filial: name, code) → `Zone` (Hudud: branch_id, name, code, location,
ISUP key/prefix) → `Camera.zone_id` (**nullable**, `ondelete=SET NULL`). The
organisation layer is optional: a camera may belong to no zone. `EzvizAccount`
holds cloud credentials (one account → many cameras). Auth principals: `User`
(panel, password + JWT cookie `vz_admin`) and `ApiClient` (API key `vz_…`, JSONB
`scopes = {"all", "branches", "zones", "cameras"}`; any match grants access —
`services/access.py`).

Panel routes: `/api/branches`, `/api/zones`, `/api/cameras`, `/api/ezviz-accounts`,
`/api/isup/*`, `/api/clients`, `/api/monitoring/*`. Integration: `/api/v1/*`,
`/ws/consumer`.

## Commands

Backend (from `server/`; no local Python on the dev box — run in the image):
```bash
docker build -t vizora-server:dev server
docker run --rm -v "$PWD/server:/app" -w /app --entrypoint sh -e JWT_SECRET=test-secret-xxxxxxxxxxxxxxxxxxxxxxxx vizora-server:dev -c "pip install -q -r requirements-dev.txt && python -m pytest -q"
alembic revision --autogenerate -m "msg"   # after a model change
alembic upgrade head
```

Panel (from `panel/`):
```bash
npm install
npm run dev      # :5173; VIZORA_API=http://localhost:8010 to point at another backend
npm run build
```

Full stack: `docker compose up -d --build`. Health: `curl http://localhost:8000/health`.
Production: `bash deploy.sh` on the server; it refuses to run unless
`/etc/vizora/deploy-target` reads `vizora-prod-01`. No CI/CD (workflows are
`.yml.disabled`).

## Design (panel)

Light theme is the default. Warm paper background, graphite text, amber/orange
accent (`#e2580b` light / `#fb8a3c` dark), Sora (display) + Manrope (body) +
IBM Plex Mono. Navigation is a **top masthead** grouped as Kuzatuv / Tuzilma /
Ulanishlar, with a sub-bar (breadcrumb, search, date); on ≤1100px it becomes a
right-side drawer, on ≤640px a bottom tab-bar. Never hard-code colours in CSS or
JSX — add a token in `lib/theme.jsx` for both themes.

## Invariants carried over from the parent project (do not regress)

The parent's `CLAUDE.md` explains each of these with measurements; the tests guard them.
- `-forced-idr 1` is mandatory for NVENC; never pass `-fflags nobuffer` to a
  transcode input (`tests/test_publish_writer.py`).
- MediaMTX `/v3/paths/list` is paginated — use `mediamtx.list_paths()` / `get_path()`.
- The EZVIZ VTM cache is per account, the wrapper installs once, and is never
  invalidated after a stream produced packets or when the device is merely silent.
- Stream tokens carry their owner and are never re-minted from another stream token.
- RTSP URLs are validated and every ffmpeg/ffprobe call uses `-protocol_whitelist`.
- CSRF: unsafe `/api/*` (not `/api/v1`) requests must have a panel `Origin`
  (`TRUSTED_ORIGINS` for extra dev ports).
- bcrypt only via `security.averify_password`; regexes that gate input use `\Z`.
- With `ENVIRONMENT=production` the app refuses default `JWT_SECRET`,
  `AES_KEY_HEX`, `ADMIN_PASSWORD`; rotating `AES_KEY_HEX` orphans stored RTSP URLs.
