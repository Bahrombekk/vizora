# ISUP 5.0 ko'prik (`isup-bridge`)

Hik-Connect bulut yo'li (pyezvizapi → VTM relay → deshifr → transkod) sekin (4–14 s),
16 kanal limitli va ba'zan tasvirni buzadi. **ISUP 5.0** — Hikvision qurilmasining o'z
protokoli: qurilma bulut o'rniga **to'g'ridan-to'g'ri bizning serverga ulanadi**.

```
Kamera ──ISUP 17660 (tcp+udp)──▶ isup-bridge ◀──video 17661 (tcp)── Kamera
                                     │  ffmpeg -c copy (transkodsiz)
                                     ▼
          MediaMTX: isup/<DeviceID>/<kanal>[/sub]
                                     │  cam/<id> (RTSP manba, kerak bo'lsa H.264 transkod)
                                     ▼
                         panel (WS / WebRTC / LL-HLS), iste'molchi API
```

Panelda bu **`source_type="isup"`** kamera: Device ID + kanal. Backend uning RTSP URL'ini
o'zi `rtsp://mediamtx:8554/isup/<id>/<kanal>` qilib to'ldiradi, shuning uchun transkod,
probe, snapshot, WS/HLS/WebRTC — hammasi oddiy RTSP kamera kabi ishlaydi.

**Pilot natijasi (2026-09-30, prod, DS-TCG406-E fw V5.4.0):**
| O'lchov | Natija |
|---|---|
| "Video yubor" buyrug'i → birinchi paket | **0.23 s** |
| Panel (`cam/<id>`, H.264 transkod) — issiq birinchi toza kadr | **0.33–0.38 s** |
| Asl ISUP oqimi (H.265 720p) — birinchi toza kadr | 0.62–0.74 s |
| Xato kadrlar | 0 (25 MB+) |
| isup-bridge + ffmpeg copy | ~0.04 yadro, ~100 MB |

---

## 1. Qaysi oqim qachon ochiq

Backend (`backend/vizora/sources/isup.py`) har 5 s da bridge'ga **want** ro'yxatini yuboradi:

| Oqim | Qachon | Nega |
|---|---|---|
| substream `isup/<id>/<ch>/sub` | har yoqilgan ISUP kamera — **doim** | ~0.3 Mbit/s: devor (`/wall`), snapshot |
| asosiy `isup/<id>/<ch>` | kimdir ko'rayotganda + oxirgi tomoshabindan `ISUP_MAIN_IDLE_SECONDS` (60 s) | 4+ Mbit/s — 100 kamerada 24/7 bo'lsa 420 Mbit/s |
| asosiy | kodek hali aniqlanmagan (`codec=auto`) | probe H.265 ni topib transkodni yoqishi uchun |

"Ko'rayotgan" = `/stream`, `/prewarm` (hover), iste'molchi `/api/v1/.../stream` yoki WS
`subscribe` chaqirildi, **yoki** MediaMTX'da `isup/<id>/<ch>` ning o'quvchisi bor.
`/stream` asosiy oqim MediaMTX'da paydo bo'lishini kutadi (≤ `ISUP_MAIN_WAIT_SECONDS`,
odatda ~0.3 s) — aks holda transkod ffmpeg'i bo'sh path'ga ulanib chiqib ketardi.
`ISUP_MAIN_IDLE_SECONDS=0` — asosiy oqim ham 24/7 (pilot rejimi).

**Onlayn holat:** probe ffprobe qilmaydi (asosiy oqim uxlab yotadi) — qurilma bridge'da
ro'yxatdan o'tgan bo'lsa yoki `isup/<id>/...` MediaMTX'da tayyor bo'lsa onlayn. Bridge
javob bermasa holat o'zgartirilmaydi.

Backend hech narsa yubormagan bo'lsa (bridge alohida ishlatilsa) bridge eski pilot
rejimida: har ulangan qurilmaning `CHANNELS` kanallari `STREAM_TYPE` bilan 24/7.

---

## 1a. Ulanish ma'lumotlari — admin o'rnatuvchiga nima beradi

Admin qaysi kamera ulanishini va uning nomini **bilmaydi** — shuning uchun kamera emas,
**ulanish ma'lumoti** beriladi. Hududlar → kartadagi **ISUP · Sozlash** → "nechta kerak?" →
N ta juftlik: **noyob Device ID** (`<hudud kodi>-001`, `-002` …) + **alohida kalit**.
Nusxalanadi yoki CSV qilib yuklab olinadi va o'rnatuvchiga beriladi.

- Har juftlik bitta qurilma uchun: Device ID noyob, kalit faqat o'sha Device ID bilan ishlaydi.
- Qurilma shu ma'lumot bilan ulanganda **kamera o'zi qo'shiladi** (`ISUP_AUTO_CAMERA`,
  standart yoqiq): nom vaqtinchalik `«Hudud» — <Device ID>`, keyin o'zgartiriladi; 1-kanal
  (NVR'ning qolgan kanallari qo'lda).
- Holat: Kutilmoqda → Ulangan → Kamera qo'shildi. Ishlatilmaganlarni qayta nusxalash mumkin.
- **Hudud kaliti** — hududda bitta, umumiy; Device ID'ni o'zingiz belgilaydigan holatlar uchun.

## 1b. Kalitlar (Encryption Key) — hudud darajasida

Panel → **ISUP qurilmalar**. Kalit alohida obyekt va **hududga** biriktiriladi: bitta kalit
bir nechta hududga yoki hududning o'ziga (alohida kalit); kerak bo'lsa **qurilmaning o'z
kaliti**. Qurilma ulanayotganda (`ENUM_DEV_AUTH`) bridge Device ID bo'yicha BITTA kalit
qaytaradi — variantlarni sinab bo'lmaydi, shuning uchun tartib aniq:

| # | Holat | Kalit |
|---|---|---|
| 1 | qurilma bloklangan | rad (ulangan bo'lsa darhol chiqariladi) |
| 2 | qurilmaning o'z kaliti | shu |
| 3 | qurilma hududining kaliti | shu |
| 4 | reestrda yo'q, Device ID hudud **prefiksi** bilan boshlanadi (`SCH1-…`) | o'sha hudud kaliti; qurilma o'zi reestrga, o'sha hududga yoziladi |
| 5 | zaxira `ISUP_KEY` (`ISUP_ALLOW_DEFAULT_KEY=true`) | pilot uchun |
| 6 | boshqa | rad — panelda "rad etilganlar" ro'yxatida ko'rinadi |

- Reestrdagi qurilma kaliti backend'da to'liq hal qilinadi (`dev`/`block` qatorlari) —
  hududi kalitsiz qurilma begona hudud prefiksiga tushib, boshqaning kaliti bilan kirmaydi.
- Kalit o'zgarsa/bekor qilinsa/qurilma bloklansa bridge ulangan qurilmani **chiqarib
  yuboradi** — u eski kalit bilan ishlab qolmaydi.
- Kalit qaytariladigan AES-256-GCM da saqlanadi (bridge'ga ochiq matn kerak). Ochiq matnni
  faqat **admin** ko'radi, har ko'rish/almashtirish logga yoziladi.
- Hudud kalitini **almashtirish** shu hudud(lar)ning barcha qurilmasini uzadi — har birida
  kalit qo'lda yangilanmaguncha ulanmaydi. Bitta qurilma kaliti sizsa — unga o'z kalitini bering.
- Bridge qayta ishga tushsa kalitlar xotiradan yo'qoladi; backend `keys_set:false` ni
  ko'rib ≤5 s da qayta yuboradi (o'lchandi: 3 s).
- Hamma qurilmaga kalit berilgach `ISUP_ALLOW_DEFAULT_KEY=false` — kalitsiz/noma'lum
  qurilma umuman ulana olmaydi.
- **Tekshirilmagan:** `ENUM_DEV_AUTH` da `pOutBuffer` (Device ID) real qurilmada kelishi.
  Kelmasa bridge logida `RAD ETILDI: id=?` (yoki zaxira kalit bilan kiradi) — birinchi
  real qurilmada shuni ko'ring.

---

## 2. Papka tarkibi

| Fayl | Vazifasi |
|---|---|
| `bridge.cpp` | C++: CMS ro'yxat, video qabul, ffmpeg'ga uzatish, nazoratchi, ichki HTTP API |
| `Dockerfile` | g++ bilan kompilyatsiya → ubuntu:22.04 + ffmpeg |
| `sdk/` | Hikvision ISUP SDK Linux64 `.so` — **REPODA YO'Q**, o'zingiz qo'yasiz (§3) |
| `inc/` | SDK header'lari, **UTF-8 ga o'girilgan** — **REPODA YO'Q** |

Servis `docker-compose.yml` / `docker-compose.prod.yml` da **`isup` profili** ortida —
SDK yo'q joyda (masalan GitHub'dan yangi klon) stek odatdagidek ko'tariladi.

**Ichki API** (port 8091, faqat Docker tarmog'ida):
`GET /health`, `GET /devices` (ulangan qurilmalar + oqimlar), `PUT /want` (matn: har
qatorda `<DeviceID> <kanal> <0|1>`). `ISUP_BRIDGE_TOKEN` berilsa `X-Bridge-Token` shart.

---

## 3. SDK qayerdan

- **Rasmiy:** Hikvision TPP → ONLINE CASE (2026-10-02, loyiha "Vizora Video Platform",
  protokol "ISUP SDK"). Ochiq yuklab olishda YO'Q. Kelsa — Linux64 arxivdagi `.so` larni
  `sdk/` ga, header'larni `inc/` ga, keyin `docker compose build isup-bridge`.
- **Pilotda (norasmiy, faqat sinov):** github.com/135356/hikvision_isup release 0.2.0
  (`hikvision_isup_0.2.0_Linux_x86_64.tar.gz` — ichida YANA gzip), `build/` dagi `.so` +
  `HCAapSDKCom/`. HCISUPCMS 2.5.0.101 (2022-11-26). Header'lar: `include/hikvision/*.h`.
  Prod serverdagi nusxa: `/opt/isup-pilot/sdk`, `/opt/isup-pilot/inc`.
- Header'lar GBK — g++ o'qiy olmaydi, UTF-8 ga o'giring:
  `for f in *.h; do iconv -f GBK -t UTF-8 -c "$f" > ../inc/"$f"; done`

`sdk/` da kerak: `libHCISUPCMS.so libHCISUPStream.so libhpr.so libHCNetUtils.so
libcrypto.so(.1.0.0) libssl.so(.1.0.0) libz.so libsqlite3.so libNPQos.so libHCISUPAlarm.so
libHCISUPSS.so HCAapSDKCom/`.

---

## 4. Ishga tushirish

`.env` (ildizda) ga:
```
COMPOSE_PROFILES=isup
ISUP_KEY=<8-16 harf/raqam — qurilmalardagi Encryption Key>
ISUP_PUBLIC_IP=<qurilma YETIB KELADIGAN IP>
```
```bash
docker compose up -d --build isup-bridge
docker compose up -d app        # ISUP_* muhitini olsin
docker compose logs -f isup-bridge | grep -E "^[0-9]{2}:"
```
Kutilgan: `ISUP ko'prik tayyor: ro'yxat 17660 (tcp/udp), video 17661 (tcp), API 8091 ...`.
Migratsiya: `0012_camera_isup` (`alembic upgrade head`; `deploy.sh` o'zi bajaradi).

Firewall: **17660 tcp+udp** va **17661 tcp** kiruvchi. Prod'da bu **Kerio Control** dagi
MAP qoidasi (+ «Применить») — serverdagi `ufw` yetarli emas. Lokal kompyuter router
ortida bo'lsa routerda ham port yo'naltirish kerak.

### Pilotdan (`/opt/isup-pilot`) o'tish — prod

1. `docker stop isup-bridge && docker rm isup-bridge` (pilot konteyneri portlarni band qilgan).
2. `cp -r /opt/isup-pilot/sdk /opt/isup-pilot/inc <repo>/isup/`
3. Repo `.env` ga `COMPOSE_PROFILES=isup`, `ISUP_PUBLIC_IP=<PUBLIC_IP>` va **pilotdagi
   bilan bir xil** `ISUP_KEY` (`/opt/isup-pilot/.env`) — kameralar shu kalit bilan ulangan.
4. `bash deploy.sh`
5. Panelda pilot kamerani (`e8c842d9-…`, hozir RTSP `isup/TCG406TEST/1`) tahrirlab manbani
   **ISUP**, Device ID `TCG406TEST`, kanal 1 qiling.

---

## 5. Qurilmani sozlash (har qurilmada bir marta)

Kamera web → **Configuration → Network → Advanced → Platform Access → ISUP**:

| Maydon | Qiymat |
|---|---|
| Enable | yoqilgan |
| Protocol Version | v5.0 |
| Address Type / Server Address | `ISUP_PUBLIC_IP` |
| Server Port | 17660 |
| Device ID | noyob, faqat `A-Z a-z 0-9 _ -` (masalan `TCG406TEST`) |
| Encryption Key | `ISUP_KEY` |

Save → 10–20 s → **Registration Status: Online**. Ko'p muvaffaqiyatsiz urinishdan keyin
kamera kutish rejimiga o'tishi mumkin — **ISUP'ni o'chirib-yoqib Save** qilsa darhol urinadi.
Keyin panel → Kameralar → Yangi kamera → **ISUP · Hikvision** — ulangan qurilma ro'yxatda
chiqadi, tanlab qo'shiladi.

Kodek: kamerada **H.264** bo'lsa transkodsiz (CPU ~0). H.265 bo'lsa probe `transcode=True`
qiladi (~0.3 yadro har ko'rilayotgan kamera). 100 kameraga yetish uchun kamerani H.264 ga o'tkazing.

---

## 6. Diagnostika

```bash
ss -lntup | grep -E ":1766[01]\b"                      # port tinglanyaptimi
tcpdump -ni enp6s18 "port 17660 or port 17661"         # paket kelyaptimi (prod)
docker compose exec app python -c "import urllib.request;print(urllib.request.urlopen('http://isup-bridge:8091/devices').read())"
```

| Log qatori | Ma'nosi |
|---|---|
| `QURILMA ULANDI: id=...` | Kamera ro'yxatdan o'tdi |
| `... ruxsat etilmagan belgi — oqim ochilmaydi` | Device ID'ni o'zgartiring |
| `KALIT XATO` | Encryption Key mos emas |
| `[api] want yangilandi: N ta oqim` | Backend ro'yxat yubordi |
| `[vaqt] birinchi video paket: so'rovdan X s` | Video keldi |
| `oqim to'xtadi — keyinroq qayta urinamiz` | Video kelmadi (kanal/substream yo'q) — 3→60 s backoff |
| `endi kerak emas — yopildi` | Asosiy oqim tomoshabinsiz yopildi |
| `EHOME50_ERROR` + `Accept` | Kamera emas narsa ulandi (curl/Test-NetConnection) — normal |

**Pilot saboqlari:**
- ffmpeg `env -u LD_LIBRARY_PATH` bilan — SDK eski OpenSSL/zlib olib keladi.
- `-fflags nobuffer` ISHLATILMAYDI (CLAUDE.md: birinchi IDR yutiladi).
- `StartPushRealStream` sinxron va ~5 s qaytadi (video undan oldin oqadi) — shuning uchun
  har oqim alohida thread'da ochiladi; ketma-ket ochilsa 100 kamera 8 daqiqa olardi.
- SDK callback hech qachon bloklanmaydi — 8 MB navbat, alohida writer (`_FfWriter` saboqi).
- `<PUBLIC_IP>:7660` da boshqa narsa javob beradi — shuning uchun 17660/17661.
- Prod: Proxmox VM `<INTERNAL_IP>`, Kerio `<FIREWALL_IP>` hamma kiruvchini
  `<ROUTER_IP>` dan SNAT qiladi.
- `pkill -f <naqsh>` ssh buyrug'ining o'zini o'ldiradi — naqsh buyruq matnida bo'lmasin.

---

## 7. Ochiq savollar / keyingi ishlar

- **Har qurilmaga alohida kalit** (`HMAC(master, DeviceID)`) — hozir bitta `ISUP_KEY`.
- Hozir istalgan Device ID ro'yxatdan o'tadi (oqim faqat panelda qo'shilganlarga ochiladi).
- NVR kanallarini qurilmadan avtomatik olish (hozir kanal qo'lda).
- Ovoz yo'q (`-an`). ANPR hodisalari — SDK Alarm moduli orqali.
- NVR (DS-7604NXI) da ISUP va Hik-Connect bir vaqtda ishlaydimi — tekshirilmagan.
- Pilot kamera 15:03 da nima sababdan ulangani aniqlanmagan (14:56 gacha 0 paket).

Ko'rib chiqilgan muqobillar (qayta tadqiq qilmaslik uchun): GB28181 (DS-TCG406-E da yo'q),
EZVIZ Open Platform (faqat H.264, HLS 10 s+), Hik-Connect P2P (norasmiy), VPN + RTSP
(obyektda dastur o'rnatib bo'lmadi), Hik-DeviceGateway (SDK kelmasa zaxira).
