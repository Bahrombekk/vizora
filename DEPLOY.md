# Deploy qo'llanmasi (production)

## Umumiy oqim
- **Deploy QO'LDA** — serverga SSH bilan kirib `bash deploy.sh` (target tekshiruvi →
  git pull → build → MIGRATSIYA app'dan OLDIN → `up -d` → tozalash). Yagona manba
  shu skript; tartib kafolatlangan. GPU overlay avtomatik qo'shiladi (§3.5).
- **CI/CD YO'Q** — server ichki (private) tarmoqda, GitHub runner'lar unga ulana
  olmaydi. Ikkala workflow `.disabled` kengaytmasi bilan turibdi
  (`.github/workflows/ci.yml.disabled`, `deploy.yml.disabled`) — YAML buzilmagan,
  kerak bo'lsa nomni `.yml` ga qaytarish kifoya (§2). Deploy oldidan testlar va
  frontend build'ni **lokalda** o'tkazing:
  `cd server && python -m pytest -q`, `cd panel && npm run build`.
- **Production stack** — `docker-compose.prod.yml` (Nginx + build qilingan frontend, app `--reload`siz,
  MediaMTX portlari yopiq, `ENVIRONMENT=production`).
- **Ikki backend konteyneri** (2026-11-03): `app` (`APP_ROLE=api` — panel, REST, fon
  vazifalari, EZVIZ publisher'lar) va `media` (`APP_ROLE=media` — `/ws/stream`, `/hls`,
  `/whep`). Bitta image; `web` nginx video yo'llarini `media` ga yuboradi. Video
  muammosida `docker compose logs media`, panel/kamera ochilishi muammosida `logs app`.
- **Front door** — `web` konteyner faqat `127.0.0.1:8095` ga ochiladi; tashqi
  trafik **host nginx** (reverse proxy) + **openresty front-proksi** (TLS) orqali keladi.
  Domen: `vizora.example.com`. Batafsil: §3.

---

## 1. Bir martalik: server tayyorlash

Serverda (Ubuntu misol):

```bash
# Docker + compose plugin
curl -fsSL https://get.docker.com | sh

# Loyihani klonlash
git clone <REPO_URL> /opt/vizora
cd /opt/vizora

# Production .env yaratish (REAL sirlar bilan!)
cp .env.example .env
nano .env
```

`.env` da albatta o'zgartiring:
```env
POSTGRES_PASSWORD=<kuchli-parol>
JWT_SECRET=<openssl rand -hex 32>       # zaif/qisqa qiymat rad etiladi
AES_KEY_HEX=<openssl rand -hex 32>      # aniq 64 hex belgi
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<kuchli-parol>           # kamida 8 belgi
TRUSTED_PROXY_COUNT=3                   # openresty → host nginx → web konteyner (§3) — SHART
MEDIAMTX_PASSWORD=<openssl rand -hex 24>   # backend ↔ MediaMTX ichki paroli — SHART
ISUP_MTX_PASSWORD=<openssl rand -hex 24>   # isup-bridge → MediaMTX (faqat isup/ publish) — SHART
```
> Oxirgi uchtasisiz `docker-compose.prod.yml` umuman ishga tushmaydi (2026-11-03 audit).
> Ikkala MediaMTX paroli faqat ichki — hech kimga berilmaydi, qayta yaratsa bo'ladi
> (`mediamtx`, `app`, `isup-bridge` birga qayta ishga tushirilsa kifoya).
> `ENVIRONMENT` `development`'dan boshqa har qanday qiymatda app default/zaif sirlar
> bilan **ishga tushmaydi** (fail-closed himoya).

Serverni deploy target sifatida ro'yxatdan o'tkazing (busiz `deploy.sh` ishlamaydi
— bu noto'g'ri serverga deploy bo'lishidan himoya):
```bash
mkdir -p /etc/vizora
echo vizora-prod-01 > /etc/vizora/deploy-target
```

Serverga GitHub'dan kod tortish uchun SSH kalit kerak (`deploy.sh` ichida
`git pull` bor). Kalitni yarating va **public** qismini GitHub repo →
Settings → Deploy keys ga qo'shing:
```bash
ssh-keygen -t ed25519 -f /root/.ssh/github_deploy -N ""
cat /root/.ssh/github_deploy.pub
```

Birinchi ishga tushirish (tavsiya — to'g'ri tartibni o'zi bajaradi):
```bash
./deploy.sh   # target tekshiruvi → git pull → build → migratsiya (app'dan OLDIN) → up -d → tozalash
```
> Migratsiya app'dan **oldin** ishlashi shart: app startup `users` jadvalini
> so'raydi — aks holda bo'sh bazada crash-loop bo'ladi. `deploy.sh` shu tartibni
> kafolatlaydi (`compose run --rm app alembic upgrade head`, keyin `up -d`).
Panel: `https://vizora.example.com/` (host nginx + openresty orqali — §3).
Login: `admin` / (`.env` dagi parol).

---

## 2. GitHub Actions (HOZIRDA O'CHIQ — qayta yoqish uchun ma'lumot)

Workflow'lar `.disabled` kengaytmasida turibdi, chunki server faqat ichki
tarmoqda ko'rinadi va GitHub'ning umumiy runner'lari unga SSH qila olmaydi.
Qayta yoqish uchun: faylni `ci.yml` / `deploy.yml` deb nomlang **va** MikroTik'da
port-forward oching (quyida) **va** repo variable'larini yangilang.

GitHub repo → **Settings → Secrets and variables → Actions**.

E'tibor bering: faqat `SSH_KEY` — **secret**, qolganlari **variable**
(workflow ularni `vars.*` orqali o'qiydi, `secrets.*` orqali emas).

| Nomi | Turi | Tavsif | 2026-09 dan keyingi qiymat |
|------|------|--------|----------------------------|
| `SSH_HOST` | variable | serverning **tashqi** manzili | `<EXTERNAL_IP>` — ichki `<INTERNAL_IP>` GitHub'dan ko'rinmaydi |
| `SSH_USER` | variable | SSH foydalanuvchi | `root` |
| `SSH_PORT` | variable | SSH port (ixtiyoriy, default 22) | MikroTik'dagi port-forward porti |
| `DEPLOY_PATH` | variable | serverdagi loyiha yo'li | `/var/www/vizora` |
| `SSH_KEY` | **secret** | **private** SSH kalit (public server `authorized_keys` da) | ko'chirishdan keyin qayta yaratilgan |

> **Ogohlantirish (2026-09 ko'chirishidan olingan saboq).** Bu qiymatlar repoda
> emas, GitHub'da turadi. Server almashganda ularni yangilash ESDAN CHIQSA,
> `main` ga push jimgina **eski serverga** deploy qilaverardi. Shuning uchun
> `deploy.sh` endi target serverni marker fayl orqali tekshiradi (§1) — noto'g'ri
> serverda deploy boshlanmasdan to'xtaydi.

Server NAT ortida bo'lgani uchun GitHub Actions unga to'g'ridan ulana olmaydi:
MikroTik (`<ROUTER_IP>`) da `<EXTERNAL_IP>:<port>` → `<INTERNAL_IP>:22`
port-forward ochilishi shart.

SSH kalit yaratish:
```bash
ssh-keygen -t ed25519 -f deploy_key -N ""
# deploy_key.pub  -> serverda ~/.ssh/authorized_keys ga qo'shing
# deploy_key      -> GitHub secret SSH_KEY ga to'liq nusxalang
```

Yoqilgan holda `main` ga har push avtomatik: testlar → serverga deploy →
migratsiya; qo'lda esa GitHub → **Actions → Deploy → Run workflow**.
Hozircha bularning o'rniga serverda `bash deploy.sh`.

---

## 3. Front door: host nginx (reverse proxy) + TLS

Bu serverda barcha saytlar bitta **host nginx** (master, `0.0.0.0:80` + `:443`)
orqali xizmat qiladi va **TLS front-proksida (openresty) tugatiladi**
(origin HTTP:80 da ishlaydi).
Shu sababli vizora'ning `web` konteyneri **80-portni public qilmaydi** —
faqat localhost'ga ochiladi, host nginx esa shu portga proxy qiladi.

**Arxitektura (2026-09 ko'chirishidan keyin, tekshirilgan):**
```
Internet ──(HTTPS)──> openresty <PUBLIC_IP> ──(HTTP:80)──> <EXTERNAL_IP> (tashqi IP)
                                                                      │
                                                       MikroTik <ROUTER_IP> (port-forward + masquerade)
                                                                      │
                                                          <INTERNAL_IP>:80 (host nginx)
                                                                      │
                                                          127.0.0.1:8095 (web konteyner)
                                                                      │ SPA + /api /hls /ws
                                                                      └──> app:8000 (ichki Docker tarmoq)
```

> **Domenni "burish" DNS'da emas, openresty'ning upstream sozlamasida qilinadi** —
> `example.com` ning barcha domenlari o'sha front-proksiga qaraydi. Proksini
> boshqaradigan odamga `<INTERNAL_IP>` BERILMAYDI (ichki IP, unga yeta olmaydi) —
> to'g'ri manzil `<EXTERNAL_IP>:80`.
>
> Nginx logida mijoz IP'si doim `<ROUTER_IP>` ko'rinadi (router masquerade
> qiladi) — haqiqiy IP faqat `X-Forwarded-For` da. Shu sababli
> `TRUSTED_PROXY_COUNT=3` (openresty → host nginx → web konteyner).

`docker-compose.prod.yml` da `web` port'i (host nginx bilan to'qnashmasligi uchun):
```yaml
ports:
  - "127.0.0.1:8095:80"   # public emas — host nginx shu yerga proxy qiladi
```

**Host nginx sayti** (`/etc/nginx/sites-available/vizora.example.com.conf`,
`sites-enabled/` ga symlink qilingan) — 80-portda `127.0.0.1:8095` ga proxy qiladi,
`/ws/` uchun WebSocket `Upgrade` + uzun `proxy_read_timeout`, `/hls/` uchun
`proxy_buffering off`. Jonli holat SSE (`/api/monitoring/stream`) `X-Accel-Buffering: no`
header yuboradi — nginx buferlashni shu javob uchun o'zi o'chiradi (qo'shimcha config shart emas).
Yoqish/yangilash:
```bash
ln -s /etc/nginx/sites-available/vizora.example.com.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

**Domenni burish:** `vizora.example.com` DNS'da openresty
(`<PUBLIC_IP>`) ga qaraydi va u yerda TLS tugaydi. Serverni almashtirganda
DNS'ga TEGILMAYDI — openresty'ning upstream'i `<EXTERNAL_IP>:80` ga
o'zgartiriladi (proksini boshqaradigan odamga shu manzil beriladi).

Tekshirish (yangi serverda):
```bash
# 1) noyob marker so'rovi log'da paydo bo'ladimi
M="check-$(date +%s)"; curl -s -o /dev/null "https://vizora.example.com/?$M"
grep "$M" /var/log/nginx/vizora.access.log

# 2) ETag domen orqali va lokal Host bilan bir xilmi
curl -sI https://vizora.example.com/ | grep -i etag
curl -sI -H 'Host: vizora.example.com' http://127.0.0.1/ | grep -i etag
```

---

## 3.5. GPU (NVIDIA) bilan transkod — tavsiya etiladi

EZVIZ/Hik bulut kameralari **HEVC** beradi, brauzer esa **H.264** kutadi — demak har
kamera transkod qilinadi. CPU'da bu bitta 1080p kamera uchun ~0.3–0.5 yadro (ABR
HD+SD ikki marta kodlaydi); bir necha kamera birdan ochilsa CPU to'yinadi va oqim
**buzilib/qotib** qoladi. GPU (NVENC) buni ~0 CPU bilan va toza sifatda bajaradi.

**1) Hostni tayyorlash**

```bash
nvidia-smi                                   # drayver ko'rinishi kerak
# nvidia-container-toolkit (Ubuntu/Debian):
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
# Tekshirish:
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

**2) Ishga tushirish (GPU overlay bilan)**

`deploy.sh` GPU'ni **o'zi aniqlaydi**: hostda `nvidia-smi` ishlasa va Docker'da
`nvidia` runtime ro'yxatdan o'tgan bo'lsa, `docker-compose.gpu.yml` avtomatik
qo'shiladi (jurnalda `==> GPU overlay YOQILDI` ko'rinadi). Majburiy boshqarish:

```bash
VZ_GPU=1 ./deploy.sh    # majburiy yoqish (aniqlash ishlamasa)
VZ_GPU=0 ./deploy.sh    # GPU bor bo'lsa ham CPU'da transkod
```

> `.env` dagi `COMPOSE_FILE` bu yerda **ishlamaydi** — `deploy.sh` ichidagi aniq
> `-f` bayroqlari uni bekor qiladi. Shuning uchun tanlov `VZ_GPU` orqali.

Qo'lda (deploy.sh'siz):

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.gpu.yml up -d
```

**3) Ishlayotganini tekshirish**

```bash
docker compose -f docker-compose.prod.yml exec app ffmpeg -encoders | grep nvenc
docker compose -f docker-compose.prod.yml logs app | grep -i nvenc
#   -> "NVENC (h264_nvenc) TAYYOR — GPU transkod ishlatiladi"
nvidia-smi   # kamera ochilganda Encoder foydalanishi ko'rinadi
```

Agar NVENC topilmasa `FFMPEG_HWACCEL=auto` jimgina `libx264` (CPU) ga qaytadi —
xizmat ishlashdan to'xtamaydi, faqat sekinroq bo'ladi.

**NVENC sessiya limiti (MUHIM).** GeForce kartalarda bir vaqtda ~8 enkod sessiyasi
ruxsat etiladi. Har kamera ABR uchun HD+SD beradi, ya'ni sozlamasiz ~4 kamera.
Shuning uchun standart `NVENC_SD_ON_CPU=true` — SD (360p) CPU'da kodlanadi
(~0.03 yadro) va GPU'da **~8 kamera** sig'adi. Limitga urilganda ffmpeg xato beradi
(`OpenEncodeSessionEx failed`) va CPU'ga QAYTMAYDI — publisher jurnalida ko'rinadi
(`/tmp/vizora-ezviz/pub-<camera_id>.log`).

### WebRTC ishlayotganini TEKSHIRISH (ochilish tezligining eng katta bo'lagi)

`WEBRTC_PUBLIC_HOSTS` bo'sh bo'lsa WebRTC **jimgina** ishlamaydi: pleyer HLS'ga
qaytadi va har ochilish segment buferini to'laydi. O'lchangan (bitta kamera,
ikkalasi ham sovuq start): **HLS 4.97s · WebRTC 1.85s** — har ochilishda ~3 s.

Hech qanday xato ko'rinmagani uchun buni faqat ICE nomzodlaridan bilish mumkin:

```bash
docker compose -f docker-compose.prod.yml exec mediamtx printenv MTX_WEBRTCADDITIONALHOSTS
#   -> bo'sh bo'lsa MUAMMO shu
```

To'g'ri sozlanganda WHEP javobida serverning haqiqiy manzili turadi:

```
a=candidate: ... <PUBLIC_IP> 8189 typ host    <- brauzer SHUNGA ulanadi
a=candidate: ... 172.22.0.5    8189 typ host    <- Docker ichki IP (foydasiz)
a=candidate: ... 127.0.0.1     8189 typ host    <- loopback (foydasiz)
```

Kerak bo'lgani (`.env`):

```bash
WEBRTC_PUBLIC_HOSTS=vizora.example.com   # yoki to'g'ridan public IP
WEBRTC_UDP_PORT=8189
```

va firewall'da `ufw allow 8189/udp`. Domen **proxysiz** bo'lishi shart — media
nginx'dan ham, Cloudflare'dan ham O'TMAYDI, to'g'ridan MediaMTX'ning UDP portiga
boradi. Signalizatsiya esa oddiy HTTPS: tashqi proxy `/whep/` ni backend'ga
uzatishi kerak (tekshirish: autentifikatsiyasiz `POST /whep/...` **401** qaytarsa
yo'l to'g'ri, **404** qaytarsa proxy'da `location /whep/` yo'q).

Sozlamalar ro'yxati: `.env.example` → "Transkod" va "Bulut oqim sifati" bo'limlari.

---

## 4. Foydali buyruqlar (serverda)

```bash
docker compose -f docker-compose.prod.yml logs -f app      # backend loglar
docker compose -f docker-compose.prod.yml ps               # holat
docker compose -f docker-compose.prod.yml restart app      # qayta ishga tushirish
docker compose -f docker-compose.prod.yml down             # to'xtatish
```
