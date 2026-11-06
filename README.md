# Vizora

Xususiy kompaniya uchun korporativ video nazorat platformasi: filial/hudud IP-kameralarini bitta markaziy tizimga yig'ish, online/offline
holatini kuzatish va WebSocket orqali boshqa tizimlarga real video oqimini
ulashish xizmati.

Ishlab chiquvchi qo'llanmasi: [`CLAUDE.md`](./CLAUDE.md).
Production'ga chiqarish (qo'lda `deploy.sh`, CI/CD yo'q): [`DEPLOY.md`](./DEPLOY.md).

## Stack
FastAPI · PostgreSQL · Redis · MediaMTX · React (Vite) · Docker.
Kamera manbalari: RTSP, EZVIZ / Hik-Connect bulut, Hikvision ISUP 5.0.

## Loyiha tuzilmasi
```
server/vizora/
  main.py, lifespan.py   ilovani yig'ish; ishga tushish va fon vazifalari
  core/                  config, db, security, crypto, logbuf
  api/                   faqat router'lar: panel/ (admin panel), integration/ (tashqi API), media/ (/hls /whep /ws/stream)
  services/              camera_streams (oqim orkestratori), access, probe, stats, snapshot
  media/                 MediaMTX, stream token/URL, WS hub, RTSP o'quvchi, ffmpeg
  sources/               kamera manbalari: ezviz/, isup.py
panel/src/
  app/ (marshrutlar, Layout) · api/client.js · player/ (Player, wsplayer) · lib/ · components/ · pages/
isup/                    isup-bridge (C++, SDK repoda yo'q — isup/README.md)
```
Qatlam qoidasi: [`CLAUDE.md`](./CLAUDE.md).

## Tez ishga tushirish (Docker)

```bash
cp .env.example .env          # qiymatlarni o'zgartiring (JWT_SECRET, AES_KEY_HEX)
docker compose up -d --build  # postgres, redis, mediamtx, backend

# Birinchi ishga tushirishda SHART — jadvallarni yaratadi (busiz: relation "users" does not exist):
docker compose exec app alembic upgrade head
docker compose restart app media

# Backend sog'ligini tekshirish:
curl http://localhost:8000/health
```

Portlar:
| Xizmat | Port |
|--------|------|
| Panel (nginx + SPA, `web`) | 8080 |
| Backend (FastAPI) | 8000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| MediaMTX WebRTC media (brauzer to'g'ridan) | 8189/udp |
| ISUP 5.0 (`isup` profili yoqilganda) | 17660 tcp+udp, 17661 tcp |

MediaMTX'ning RTSP/HLS/API portlari hostga **ochilmaydi** — faqat Docker ichki
tarmog'i; tashqariga oqim backend proxy'lari (`/hls`, `/whep`, `/ws/stream`) orqali,
token bilan chiqadi.

## Lokal ishlab chiqish (Docker'siz backend)

```bash
cd server
py -3 -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env
uvicorn vizora.main:app --reload
```

Migratsiya (sxema `0001_initial` da; model o'zgarsa yangi revision):

```bash
cd server
alembic upgrade head
alembic revision --autogenerate -m "o'zgarish tavsifi"   # model o'zgargach
```

## Frontend (dev)

```bash
cd panel
npm install
npm run dev          # http://localhost:5173  (API/HLS -> :8000 proxy)
npm run build        # dist/ -> production'da backend xizmat qiladi
```

## Admin

Birinchi ishga tushishda standart admin yaratiladi: **`admin` / `admin`**
(`.env` da `ADMIN_USERNAME` / `ADMIN_PASSWORD` orqali o'zgartiriladi —
production'da albatta o'zgartiring).

Login: `POST /api/auth/login` (form: `username`, `password`) → JWT **HttpOnly
cookie** (`vz_admin`, SameSite=Lax) o'rnatiladi (XSS himoyasi — token JS'da
saqlanmaydi). Chiqish: `POST /api/auth/logout`.

**Parolni almashtirish:** panelda yon menyu pastidagi qulf tugmasi
(`POST /api/auth/change-password` — joriy parol + yangi parol, kamida 8 belgi).
`.env` dagi `ADMIN_PASSWORD` faqat birinchi ishga tushishda (baza bo'sh
bo'lganda) ishlaydi — keyin uni o'zgartirish parolni o'zgartirmaydi.
API hujjatlari: <http://localhost:8000/docs>.

## Holat

**Bosqich 0 — skelet** ✅
- [x] Docker Compose: postgres + redis + mediamtx + backend
- [x] FastAPI skelet: config, async DB, `/health`, CORS, SPA xizmat
- [x] Alembic (async) + React + Vite skelet

**Bosqich 1 — model + REST CRUD** ✅
- [x] ORM: branches / zones / cameras / users / api_clients
- [x] AES-256-GCM RTSP shifrlash, bcrypt parol, JWT auth
- [x] CRUD: branches, zones, cameras (+ filtr, bulk import)
- [x] Admin login + API mijoz (key) boshqaruvi
- [x] Init migratsiya + e2e smoke test o'tdi

**Admin panel (frontend)** ✅
- [x] Login (JWT) + himoyalangan route'lar
- [x] Boshqaruv paneli (filial/hudud/kamera + online/offline hisoblari)
- [x] Filiallar CRUD
- [x] Hududlar CRUD (filial bo'yicha filtr)
- [x] Kameralar CRUD (filial→hudud→holat filtr, kodek/transkod/faollik)

Ishga tushirish: `cd panel && npm install && npm run dev` →
<http://localhost:5173> (login: `admin` / `admin`).

**Bosqich 2 — jonli oqim** ✅
- [x] MediaMTX Control API integratsiya (kamera CRUD'da path avtomatik sozlanadi)
- [x] On-demand RTSP pull (oqim faqat ko'rilganda boshlanadi)
- [x] HEVC→H.264 transkod (runOnDemand ffmpeg, 2s GOP)
- [x] HLS proxy (bir xil origin, MediaMTX cookieCheck redirectni hal qiladi)
- [x] Frontend hls.js player + "Ko'rish" tugmasi (keyin WS+WebCodecs → WebRTC → HLS zanjiriga o'tdi, pastda)
- [x] Haqiqiy kamerada e2e tekshirildi (1080p HEVC → H.264 HLS)

**Kamerani ko'rish:** Panel → Kameralar → **Ko'rish**. Birinchi marta transkod
ishga tushishi ~5-10s oladi (on-demand), keyin jonli ko'rinadi.

**Bosqich 3 — online/offline probe** ✅
- [x] Fon vazifasi: kameralarni davriy RTSP probe (ffprobe, TCP, semaphore)
- [x] Oqayotgan kamera (MediaMTX ready) → to'g'ridan online
- [x] Holat PostgreSQL'da yangilanadi + Redis pub/sub (`camera:status`)
- [x] Real-time push (SSE) — Monitoring sahifasi darhol yangilanadi (7-bosqich)
- [x] Haqiqiy kamerada tekshirildi: `offline → online`

Sozlamalar (`.env`): `PROBE_INTERVAL_SECONDS` (15), `OFFLINE_AFTER_SECONDS` (45),
`PROBE_CONCURRENCY` (20), `EZVIZ_STATUS_INTERVAL_SECONDS` (60 — EZVIZ buluti
alohida sekinroq tekshiriladi).

**Bosqich 4 — iste'molchi WebSocket API + oqim xavfsizligi** ✅
- [x] `/ws/consumer?api_key=…` — API key auth (SHA-256 lookup) + scope
- [x] `subscribe` → `stream_ready` (tokenli HLS URL), `unsubscribe`, `list`, `status`
- [x] `status_changed` real vaqt push (Redis pub/sub)
- [x] Oqim tokeni (kameraga bog'langan, qisqa muddatli JWT)
- [x] HLS proxy auth: `?token` / cookie / admin Bearer (tokensiz → 401)
- [x] MediaMTX portlari hostdan yopildi (faqat ichki tarmoq + /hls proxy)
- [x] `GET /api/cameras/{id}/stream` (admin Player uchun tokenli URL)
- [x] e2e tekshirildi (WS subscribe → online HLS + auth 401/200)

### Iste'molchi (tashqi tizim) uchun
```
ws://SERVER/ws/consumer?api_key=<key>
 → {"type":"subscribe","camera_id":"<uuid>"}
 ← {"type":"stream_ready","hls_url":"/hls/cam/<id>/index.m3u8?token=…","status":"online"}
```
API key olish (admin): `POST /api/auth/clients {"name":"...","scopes":{"all":true}}`.

**Bosqich 5/6 — mustahkamlik, to'liqlik, production** ✅
- [x] Probe tolerantligi (`offline_after_seconds`) — bir martalik xatoda offline qilmaydi
- [x] Kodek avto-aniqlash (probe) — HEVC topilsa transcode avto-yoqiladi
- [x] RTSP test endpoint (`POST /api/cameras/test`) + formada "Sinash" tugmasi
- [x] Pagination (`limit`/`offset` + `X-Total-Count`) + frontend pager
- [x] Dashboard summary endpoint (count'lar to'liq ro'yxatsiz)
- [x] API mijozlar UI (sahifa) + bulk import UI + MediaMTX path re-ensure
- [x] Production: prod'da default sir guard, login rate-limit (Redis), Nginx (SPA + proxy)
- [x] Backend pytest: crypto, stream token, security, scope, kodek

**Bosqich 7 — xavfsizlik mustahkamlash + jonli monitoring** ✅
- [x] Admin JWT → **HttpOnly cookie** (`vz_admin`, SameSite=Lax); `localStorage`'dan olib tashlandi (XSS)
- [x] HLS proxy **path-traversal** tuzatildi (qat'iy `cam/<uuid>` + suffiks allowlist)
- [x] Login rate-limit: ishonchli proksi (XFF spoofing) + per-username; `scopes` Pydantic validatsiya
- [x] Fail-closed sir guardlari (zaif/qisqa JWT, non-hex AES); EZVIZ deshifrlash guard
- [x] **EZVIZ oqim barqarorligi**: uzoq-yashar ffmpeg — bulut blip'ida muxer reset bo'lmaydi (kam uzilish)
- [x] Tez probe (15s) + EZVIZ buluti alohida cadence (60s); offline tolerantlik 45s
- [x] **Jonli Monitoring sahifasi** — SSE push (`/api/monitoring/stream`), barcha kameralar holati
- [x] MediaMTX Control API auth (opsional, `MEDIAMTX_API_USER/PASSWORD`); unique `api_key_hash` (mig 0008)
- [x] Migratsiya CI job (upgrade/downgrade/drift) + 57 backend test (o'sha paytda)

**Bosqich 8 — bulut kameralar, tez ochilish, ISUP, tuzilma (2026-09…10)** ✅
- [x] EZVIZ / Hik-Connect bulut kameralari (RTSP'siz): on-demand publisher, shifrlangan oqimni deshifrlash, GPU (NVENC) yoki CPU transkod
- [x] Panel pleyeri: **WebSocket + WebCodecs → WebRTC (WHEP) → LL-HLS** — birinchi muvaffaqiyatlisi ishlatiladi; hls.js faqat zaxira yo'lda yuklanadi
- [x] Transkodsiz xom HEVC yo'li (`/raw`) — faqat darhol ochiladigan holatda; sovuq ochilish H.264 (1s I-kadr)
- [x] Jonli devor (`/wall`) — substream plitkalar; snapshot posterlar (EZVIZ + RTSP)
- [x] Hikvision **ISUP 5.0** — qurilma bizga o'zi ulanadi; kalitlar hudud darajasida, NVR kanallari avtomatik
- [x] "Oqim yo'q" (`no_stream`) holati, Monitoring'da sabab va publisher logi
- [x] Xavfsizlik auditi (in'ektsiya, SSRF, sessiyalar, CSRF, DoS)
- [x] Kod tuzilmasi qatlamlarga ajratildi; oqim mantig'i bitta orkestratorda (`services/camera_streams.py`)

### Monitoring (jonli holat)
Panel → **Monitoring**: barcha kameralar online/offline holati, rangli, **real-time**
yangilanadi (poll emas — SSE). Filtr (online/offline/xato) + qidiruv. Backend
`probe` o'zgarishni Redis'ga e'lon qiladi → SSE darhol brauzerga uzatadi.

### Testlar
```bash
cd server
pip install -r requirements-dev.txt   # bir marta (pytest kiradi)
python -m pytest -q                    # 285 test (Postgres/Redis'siz ishlaydi)
```
Lokal Python bo'lmasa — testlarni app image'ida:
`docker run --rm -v "$PWD/server:/app" -w /app -e JWT_SECRET=test-secret-xxxxxxxxxxxxxxxxxxxxxxxx vizora-app sh -c "pip install -q pytest pytest-asyncio && python -m pytest -q"`.

Migratsiya yaxlitligi (Postgres kerak): `alembic upgrade head && alembic downgrade base
&& alembic upgrade head` + `alembic check`. **CI hozir o'chiq** (server ichki tarmoqda,
GitHub runner'lari yetmaydi — `.github/workflows/*.yml.disabled`), shuning uchun deploy
oldidan testlar va `npm run build` qo'lda ishga tushiriladi ([`DEPLOY.md`](./DEPLOY.md)).

### Production (Nginx + build qilingan frontend)
```bash
docker compose up -d --build web    # http://localhost:8080  (SPA + /api + /hls + /ws)
```
Prod'da `.env` da `JWT_SECRET`, `AES_KEY_HEX`, `ADMIN_PASSWORD` ni almashtiring
(`ENVIRONMENT` `development`'dan boshqa har qanday qiymatda default/zaif sirlar bilan
ishga tushmaydi). Proksi ortida `TRUSTED_PROXY_COUNT=1` qo'ying (login throttle to'g'ri ishlashi uchun).
