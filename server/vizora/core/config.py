"""Ilova sozlamalari — barchasi muhit o'zgaruvchilaridan (.env) o'qiladi."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Umumiy
    app_name: str = "vizora"
    environment: str = "development"  # development | production
    debug: bool = True
    # Jarayon roli: all — hammasi bitta jarayonda (dev, vite); api — panel/REST/fon
    # vazifalari; media — faqat video (/ws/stream, /hls, /whep). Compose `api` + `media`
    # ni alohida konteynerda ishga tushiradi: ko'p tomoshabinda video API'ni bo'g'masin.
    app_role: str = "all"

    # Ma'lumotlar bazasi (async asyncpg)
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/vizora"

    # Redis (holat + pub/sub)
    redis_url: str = "redis://localhost:6379/0"

    # MediaMTX
    mediamtx_api_url: str = "http://localhost:9997"   # control API
    mediamtx_hls_url: str = "http://localhost:8888"   # HLS chiqish (proxy uchun)
    mediamtx_rtsp_host: str = "localhost"
    mediamtx_webrtc_url: str = "http://localhost:8889"   # WHEP (WebRTC) chiqishi

    # --- WebRTC (WHEP) ---
    # Panelda HLS o'rniga WebRTC: HLS'da pleyer 2 segment (~2s) buferlashi shart,
    # WebRTC'da esa birinchi kadr kelishi bilan ko'rsatiladi -> ochilish ~2s tezroq
    # va kechikish <1s (mobil ilova kabi). Iste'molchilar (ApiClient) HLS'da qoladi.
    # SHART: MediaMTX ICE porti (UDP 8189) brauzerga YETIB BORISHI kerak va
    # `MTX_WEBRTCADDITIONALHOSTS` da server public IP/domeni ko'rsatilishi kerak —
    # Cloudflare proxy UDP'ni o'tkazmaydi. Shu sozlanmagan bo'lsa WebRTC ulanmaydi
    # va pleyer jimgina HLS'ga qaytadi (buzilmaydi).
    webrtc_enabled: bool = True
    # --- WebSocket xom oqim (WebCodecs) ---
    # Panel uchun ENG TEZ yo'l: backend MediaMTX'dan kodlangan kadrlarni RTSP orqali
    # oladi (dekod/enkodsiz) va WebSocket bilan uzatadi; brauzer WebCodecs bilan
    # chizadi. HLS'dagi segment kutish yo'q, WebRTC'dagi UDP port talabi yo'q
    # (oddiy HTTP/WS — nginx/Cloudflare o'tkazadi). Yangi tomoshabin so'nggi GOP'ni
    # bir zumda oladi. Brauzer kodekni qo'llamasa (HEVC'siz mashina) pleyer
    # WebRTC/HLS'ga qaytadi. `vizora/services/ws_stream.py`.
    ws_stream_enabled: bool = True
    # XOM (transkodsiz) `<path>/raw` chiqishi — panel pleyeri shuni o'qiydi va
    # HEVC bo'lsa brauzerda WebCodecs bilan dekodlaydi. Asosiy path H.264 bo'lib
    # qoladi (HLS/WebRTC/iste'molchilar tegilmaydi). Sovuq ochilishdan ~1.4s
    # oladi (enkoder init + birinchi kalit kadrni kutish yo'qoladi) va sifat
    # qayta kodlanmagan asl oqim bo'ladi. `ezviz/publish._raw_output`.
    ezviz_raw_path: bool = True
    # Nosoz ("oqim yo'q") kameralarni qayta tekshirish oralig'i. Nosozlik faqat
    # ko'rish paytida aniqlanadi, shuning uchun tuzalganini bilish uchun fon
    # tekshiruvi kerak ([[manager.run_fault_checker]]). Bir siklda 2 kamera.
    ezviz_fault_recheck_seconds: float = 900.0
    # Oxirgi tomoshabin ketgach RTSP o'quvchini shu vaqtdan keyin yopamiz. Hub ochiq
    # turgan paytda uning GOP-keshi tirik — kamerani qayta ochgan tomoshabin birinchi
    # kadrni DARHOL oladi. 3s edi: past fps'li NVR kanallarida I-kadrlar orasi 2-8s
    # (o'lchandi, 2026-10-03), ya'ni har qayta ochilish kameraning navbatdagi
    # I-kadrini (12-29s gacha) kutardi.
    ws_stream_idle_seconds: float = 30.0
    # Manba (EZVIZ publisher / on-demand RTSP) tayyor bo'lishini shu vaqtgacha kutamiz:
    # DESCRIBE 404 — path bor, oqim hali yo'q. EZVIZ sovuq start odatda 6-20s, lekin
    # shifrlangan Hik NVR kanallarida 60-75s kuzatilgan (2026-09-10, TCH-9). Hub shu
    # vaqt davomida brauzerga "wait" heartbeat yuboradi, pleyer taymerini qayta boshlaydi.
    ws_stream_ready_timeout: float = 90.0
    # MediaMTX ichki foydalanuvchisi — backend (API, HLS, WHEP, RTSP o'qish) va EZVIZ
    # publisher shu login bilan ulanadi. mediamtx.yml'da `vzapp` (parol compose'dan
    # MTX_AUTHINTERNALUSERS_1_PASS ga beriladi). Ilgari MediaMTX Docker tarmog'ida
    # parolsiz edi: internetga ochiq isup-bridge buzilsa, API orqali `runOnDemand`
    # bilan MediaMTX konteynerida buyruq bajarish mumkin edi (audit 2026-11-03).
    # Bo'sh parol = auth yuborilmaydi (faqat eski mediamtx.yml bilan ishlaydi).
    mediamtx_user: str = "vzapp"
    mediamtx_password: str = ""

    # --- Transkod: GPU (NVENC) yoki CPU ---
    # `auto` — NVIDIA GPU + ishlaydigan `h264_nvenc` bo'lsa GPU ishlatiladi, aks
    # holda jimgina libx264 (CPU). `nvenc` — majburan GPU. `none` — faqat CPU.
    # GPU transkod: CPU ~0, sifat aniqroq, bir necha o'nlab kamerani bir vaqtda
    # ko'tara oladi (libx264 bilan CPU to'yinib oqim buzilardi).
    # Aniqlash `vizora/services/ffmpeg_hw.py` da — bir marta, sinov kodlash bilan.
    # RTSP MANBA transporti (MediaMTX `sourceOnDemand` kameralar uchun).
    # `automatic` (MediaMTX standarti) avval UDP'ni sinaydi va paket kelmasa TCP'ga
    # o'tadi — jurnalda "no UDP packets received, switching to TCP" va ~3s BEKOR
    # kutish. Kameralarimiz TCP'da ishlaydi, shuning uchun standart `tcp`.
    # Faqat UDP'ni qo'llaydigan kamera chiqsa `automatic` ga qaytaring.
    mediamtx_rtsp_transport: str = "tcp"
    ffmpeg_hwaccel: str = "auto"
    # GPU bo'lmaganda libx264 preseti. `ultrafast` eng arzon, ammo bir xil
    # bitrate'da eng xunuk (kvadratlar); `veryfast` ~1.5x CPU, sezilarli toza.
    ffmpeg_x264_preset: str = "veryfast"
    # RTSP kameralarni transkod qiluvchi ffmpeg MEDIAMTX konteynerida ishlaydi
    # (runOnDemand) — u boshqa image, shuning uchun GPU'ni ALOHIDA yoqiladi.
    # GPU'li serverda: MEDIAMTX_TRANSCODE_ENCODER=h264_nvenc (+ mediamtx
    # konteyneriga NVIDIA runtime bering — docker-compose.gpu.yml).
    mediamtx_transcode_encoder: str = "libx264"
    # GPU'da SD (360p) rendition'ini CPU'da kodlash. GeForce kartalarda bir vaqtda
    # NVENC sessiyalari cheklangan (odatda 8); har kamera 2 chiqish (HD+SD) beradi,
    # ya'ni ~4 kamera. SD 360p CPU'da juda arzon (~0.03-0.05 yadro) — uni CPU'ga
    # berib GPU sessiyalarini IKKI BARAVAR ko'p kameraga yetkazamiz.
    nvenc_sd_on_cpu: bool = True
    # Bir vaqtda GPU'da kodlanadigan EZVIZ publisher'lar soni. GeForce NVENC
    # sessiya chegarasi (~8) oshsa ffmpeg "OpenEncodeSessionEx failed (21)" bilan
    # o'ladi va kamera umuman ochilmaydi. Shundan oshgan publisher'lar CPU'da
    # (libx264) boshlanadi — sekinroq, lekin ISHLAYDI. 2 ta zaxira MediaMTX
    # runOnDemand (RTSP HEVC) transkodlari va NVENC sinovi uchun qoldiriladi.
    nvenc_max_sessions: int = 6

    # Xavfsizlik
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    # Birinchi ishga tushishda yaratiladigan standart admin (production'da o'zgartiring)
    admin_username: str = "admin"
    admin_password: str = "admin"
    # RTSP URL'larini shifrlash uchun 32-baytlik kalit (base64 yoki hex emas — xom 32 bayt)
    # Production'da albatta o'zgartiriladi (`openssl rand -hex 32`).
    aes_key_hex: str = "0" * 64  # 32 bayt = 64 hex belgi

    # Holat probe
    probe_interval_seconds: int = 15  # RTSP tekshiruv oralig'i (jonli ko'rinish uchun tez)
    probe_concurrency: int = 20      # bir vaqtda nechta kamerani tekshirish
    offline_after_seconds: int = 45  # shu vaqt javob bo'lmasa -> offline (flapping'ga qarshi tolerantlik)
    probe_timeout_seconds: float = 6.0  # har ffprobe uchun timeout (down kamera siklni cho'zmasin)
    # EZVIZ bulut qurilma ro'yxati ALOHIDA, sekinroq oraliqda so'raladi (rate-limit/
    # batareya tejash) — RTSP tez tekshirilsa ham EZVIZ buluti shu oraliqda urilmaydi.
    # Oqayotgan (MediaMTX ready) EZVIZ kamera baribir har siklda darhol online bo'ladi.
    ezviz_status_interval_seconds: int = 60

    # Operator kiritgan RTSP URL ichki xizmatga (postgres, redis, mediamtx, Docker
    # tarmog'i, localhost) ishora qilsa rad etiladi — `/cameras/test` orqali ichki
    # tarmoqni skanerlash mumkin edi (audit 2026-11-03). Lokal sinov uchun false.
    rtsp_block_internal: bool = True

    # Tashqi API mijoz shu oyna ichida ko'pi bilan shuncha TURLI kamerani ocha oladi
    # ([[services.consumer_quota]]) — har biri H.264 transkod (~1 yadro). 0 = cheksiz.
    consumer_max_cameras: int = 48
    consumer_camera_window_seconds: int = 300

    # --- ISUP 5.0 (Hikvision qurilmasi bulutsiz, o'zi bizga ulanadi) ---
    # isup-bridge konteynerining ichki API'si (hostga ochilmaydi). Bo'sh = ISUP o'chiq:
    # kameralar qo'shilaveradi, lekin oqim boshqaruvi va qurilma ro'yxati ishlamaydi.
    isup_bridge_url: str = "http://isup-bridge:8091"
    isup_bridge_token: str = ""   # bridge'dagi API_TOKEN bilan bir xil (ixtiyoriy)
    # Qurilma sozlamasida ko'rsatiladigan manzil — faqat paneldagi yo'riqnoma uchun.
    isup_public_host: str = ""
    isup_cms_port: int = 17660
    # Substream (kanal 2-profili) doim ochiq turadi: ~0.3 Mbit/s, onlayn holat, devor
    # va snapshot shundan. O'chirilsa faqat asosiy oqim (so'ralganda) qoladi.
    isup_substream: bool = True
    # Asosiy oqim faqat ko'rilganda ochiladi: oxirgi tomoshabin ketgach shuncha soniya
    # kutib yopiladi. 0 = asosiy oqim ham 24/7 (pilot rejimi; 4+ Mbit/s har kamera).
    isup_main_idle_seconds: int = 60
    # /stream asosiy oqim MediaMTX'da paydo bo'lishini shuncha kutadi (ISUP ~0.3s).
    isup_main_wait_seconds: float = 6.0
    isup_sync_seconds: float = 5.0
    # Zaxira (umumiy) kalit — hudud/qurilma kaliti yo'q qurilmalar shu bilan ulanadi.
    # Pilot shu bilan ishlaydi; hamma qurilmaga kalit berilgach `false` qiling — shunda
    # ro'yxatda yo'q va kalitsiz qurilma umuman ulana olmaydi. Qiymati bridge'dagi
    # ISUP_KEY bilan bir xil (compose ikkalasiga bitta o'zgaruvchidan beradi).
    isup_allow_default_key: bool = True
    isup_default_key: str = ""
    # Reestrdagi (hududga biriktirilgan) qurilma birinchi marta ulanganda kamerasi o'zi
    # qo'shiladi — admin o'rnatuvchiga faqat "ulanish ma'lumoti" (Device ID + kalit) beradi,
    # kamera nomini bilishi shart emas (vaqtinchalik nom, keyin o'zgartiriladi).
    isup_auto_camera: bool = True

    # --- EZVIZ bulut kameralari ---
    # Hisobda region ko'rsatilmasa ishlatiladigan standart domenlar (provayder bo'yicha).
    ezviz_default_region: str = "apiisgp.ezvizlife.com"
    hik_default_region: str = "api.hik-connect.com"  # Hik-Connect global (regionga redirect)
    # Login token'lari shu papkada saqlanadi (har filial uchun bitta JSON).
    # Konteyner ichida yoziladigan vaqtinchalik joy bo'lishi kerak.
    ezviz_token_dir: str = "/tmp/vizora-ezviz"
    # Sessiya token'ini shu oraliqda yangilab turamiz (parol/MFA so'ralmasin).
    ezviz_token_refresh_seconds: int = 3600
    # HISOB darajasidagi bulut metama'lumotlari (VTM pagelist + VTDU token) keshi.
    # Bular kameraga emas, hisobga tegishli — bir hisobdagi hamma kamera uchun bir
    # xil. Keshsiz har ochilishda ~6s bekorga ketardi (o'lchangan: pagelist 4.3s +
    # vtdu 1.8s) va 200 kamerada bulut rate-limit'i xavfi bor edi. Oqim ochilmasa
    # kesh avtomatik bekor qilinib qayta olinadi, shuning uchun uzunroq TTL xavfsiz.
    # 1 soat: fon vazifasi keshni TTL/2 da yangilab turadi, ya'ni kesh amalda
    # DOIM issiq. Uzun TTL xavfsiz — ochilish muvaffaqiyatsiz bo'lsa kesh
    # avtomatik bekor qilinadi (publish.py, `if not got_packets`).
    ezviz_vtm_cache_seconds: int = 3600
    # On-demand: tomoshabin (MediaMTX reader) bo'lmasa publisher shu vaqtdan
    # keyin to'xtaydi. MUROSA: katta qiymat = qayta ulanish DARHOL (oqim issiq
    # turadi), lekin batareya/4G ko'proq sarflanadi; kichik qiymat = tejamkor,
    # lekin har safar kamera "uyqudan uyg'onadi" (~8-20s sovuq start).
    # Muhitdan: EZVIZ_IDLE_SECONDS. Faol monitoring uchun 120-300, tejash uchun 30-60.
    ezviz_idle_seconds: int = 120
    # Hik (simli NVR) kameralar batareyali emas — uzoqroq "issiq" tutiladi: bir
    # marta ko'rilgach shu vaqtgacha oqib turadi -> qayta ko'rish bir zumda, va
    # bulutga ochish/yopish churn'i ("bot" ko'rinishi) keskin kamayadi.
    hik_idle_seconds: int = 900
    # TRANSKOD qilayotgan (to'liq rejim) publisher tomoshabinsiz shuncha turadi —
    # yuqoridagi ikkalasidan qisqasi olinadi. Enkoder ~1 yadro/kamera yeydi: panel
    # sovuq ochilishda H.264 so'raydi (tez I-kadr), va 15 daqiqalik Hik muddati bilan
    # 26 ta ochilgan kamera 26 yadroni band qilib qolgan (o'lchandi, 2026-10-03).
    # WS hub o'quvchini yana `ws_stream_idle_seconds` (30s) ushlab turadi, ya'ni
    # tomoshabin ketgach enkoder jami ~90s yashaydi. Xom publisher'ga taalluqli emas.
    ezviz_transcode_idle_seconds: int = 60
    # Hik kameralarni 24/7 "issiq" tutish: fon vazifasi barcha yoqilgan Hik
    # kameralarni doimo oqitib turadi -> HAR ko'rish bir zumda (sovuq start yo'q).
    # CPU evaziga (Hik simli — batareya muammosi yo'q). Batareyali EZVIZ kirmaydi.
    # O'CHIQ (False): oldindan isitish YO'Q — kamera faqat SO'RALGANDA (Ko'rish
    # bosilganda) on-demand ishga tushadi, qolgan payt ishlamaydi. Server yukini
    # keskin kamaytiradi (warm-keeper + prewarm ham shu flag bilan o'chadi).
    # Kelishuv: birinchi ochilish sekinroq (~15-25s bulut isishi), lekin server
    # ortiqcha yuklanmaydi.
    hik_keep_warm: bool = False
    hik_warm_interval_seconds: int = 30  # CPU'ni shu oraliqda o'lchab, issiq sonini rostlaydi
    # Adaptiv CPU nazorati: issiq tutiladigan kameralar soni CPU'ga qarab o'zgaradi.
    # CPU yuqori bo'lsa kam kamera issiq (so'nggi ko'rilganlarga ustunlik), bo'sh
    # bo'lsa ko'proq. Tizim CPU'si (host) o'lchanadi — serverni ortiqcha yuklamaslik.
    hik_warm_max_cpu_percent: float = 80.0  # bundan yuqori -> issiq sonini kamaytir
    hik_warm_min_cpu_percent: float = 65.0  # bundan past -> yana bittani issiq qil
    # Ayni ko'rilmayotgan bo'lsa ham doim shuncha so'nggi kamera issiq qoladi (CPU
    # juda band bo'lsa ham) — eng faol kameralar tez ochilsin.
    hik_warm_min_cameras: int = 2
    # QAT'IY shift: byudjet CPU past bo'lganda har siklda +1 o'sadi, shu sondan
    # oshmaydi. Busiz 200 kamerali tizimda byudjet 200 tomon o'sib ketardi (faqat
    # CPU to'xtatardi) — har biri alohida publisher + ffmpeg. Bu qiymat "bir
    # vaqtda nechta kamera ISSIQ tursin" degani; qolganlari on-demand (~14s).
    hik_warm_max_cameras: int = 12
    # Idle-reaper publisher'larni shu oraliqda tekshirib turadi.
    ezviz_reaper_interval_seconds: int = 15

    # --- DOIM TAYYOR kameralar (tanlangan ro'yxat) ---
    # Bu yerdagi kameralar 24/7 issiq turadi: publisher ham, HLS muxer ham doim
    # tayyor -> "Ko'rish" bosilganda BIR ZUMDA ochiladi (sovuq start ~6s emas).
    # `hik_keep_warm` dan MUSTAQIL va idle-reaper ularga tegmaydi.
    # Narxi: har kamera ~0.15-0.38 yadro va ~120MB DOIMIY (on-demand emas), shuning
    # uchun ro'yxat QISQA bo'lishi kerak — eng ko'p ochiladigan bir nechta kamera.
    # Vergul bilan ajratilgan kamera UUID'lari; muhitdan: ALWAYS_WARM_CAMERA_IDS.
    # Standart bo'sh — panelda "Doim issiq tutish" (Camera.keep_warm) afzal.
    always_warm_camera_ids: str = ""
    # Ro'yxat shu oraliqda tekshiriladi (o'lgan publisher tiklanadi, muxer isitiladi).
    always_warm_interval_seconds: int = 20
    # ABR (tarmoqqa moslashuvchi sifat): publisher bitta dekoddan IKKI rendition
    # chiqaradi — HD (asosiy) va SD (past tarmoq uchun). hls.js o'tkazuvchanlikka
    # qarab avtomatik almashadi (yaxshi tarmoq → HD, yomon → SD). Manba juda katta
    # bo'lsa (masalan 5.7MP NVR) HD shu balandlikkacha kichraytiriladi —
    # transkod yengillashadi, sovuq start tezlashadi.
    ezviz_hd_max_height: int = 1080   # HD rendition maksimal balandligi (kichraytirish cap'i)
    ezviz_sd_height: int = 360        # SD (past tarmoq) rendition balandligi
    # HD/SD bitrate (kbit/s). Avval faqat `-maxrate` berilib CRF standart (23)
    # qolgani uchun 1080p enkoder muttasil VBV'ga urilib "kvadratlashardi" — endi
    # CBR-ga yaqin (`-b:v` = `-maxrate`) va bitrate yetarli. hls_proxy master
    # playlist'idagi BANDWIDTH shu qiymatlardan olinadi (mos bo'lishi uchun).
    ezviz_hd_bitrate_kbps: int = 3500
    ezviz_sd_bitrate_kbps: int = 800
    # Kalit kadr oralig'i (sekund) = HLS segment uzunligi. 1s → pleyer 2 segment
    # buferlab ~2s da boshlaydi (2s bo'lganda ~4-5s kutardi). Kichraytirish
    # sovuq startni tezlashtiradi, bitrate'ni ~5-10% oshiradi.
    ezviz_keyframe_seconds: float = 1.0
    # Chiqish kadr tezligi. **0 = passthrough** (manbaning o'z fps'i — mobil
    # ilova qanday ko'rsatsa shunday, ENG SILLIQ). >0 bo'lsa CFR: past/notekis
    # fps kameralarda oxirgi kadr takrorlanadi, ammo manba bundan TEZ bo'lsa
    # kadrlar TASHLANADI (25fps → 15fps = har 5 kadrdan 2 tasi yo'q) — aynan shu
    # "uzuq-uzuq" ko'rinishga sabab bo'lardi. Shuning uchun standart 0.
    ezviz_output_fps: int = 0
    # ffmpeg stdin uchun bufer (bayt). Bulut o'quvchisi ALOHIDA thread'da yozadi;
    # bufer shu hajmdan oshsa eski kadrlar kalit-kadr chegarasida tashlanadi
    # (bloklanish/paket yo'qotish o'rniga toza "jonlilikka sakrash").
    ezviz_write_buffer_bytes: int = 8 * 1024 * 1024
    # Publisher (va uning ffmpeg'i) CPU ustuvorligi: `nice` qiymati (0 = o'zgarmaydi).
    # Ko'p kamera ochilganda transkodlar CPU'ni to'ldiradi (o'lchandi: 40 tomoshabin,
    # load average 240 / 32 yadro) va panel API'si event loop'i navbat kutib 5-12 s
    # javob berardi. Past ustuvorlikda API birinchi xizmat qiladi; video esa baribir
    # buferli (pleyer jitter-buferi), ozgina kechikishni sezdirmaydi.
    publisher_nice: int = 19
    # Bulutdan SUBSTREAM (stream=2) — past o'lcham (~D1/CIF). Buziladigan yuqori-res
    # kameralarda paket yo'qolmaydi -> toza; H.264 bo'lsa transkodsiz copy (CPU ~0).
    # Endi PER-KAMERA boshqariladi (Camera.ezviz_use_substream) — GLOBAL EMAS. Bu
    # qiymat faqat cloud.make_client default'i (substream aniq berilmaganda).
    # O'CHIQ: hech bir kamera avtomatik o'zgarmaydi; admin buziladigan kameralarga
    # alohida yoqadi -> iste'molchilar toza HD kameralarda HD ni saqlaydi.
    ezviz_prefer_substream: bool = False
    # SNAPSHOT (grid uchun "ko'zni aldash"): jonli transkod o'rniga har kameradan
    # davriy BITTA rasm (JPEG) olib keshlaymiz (pyezvizapi capture_picture). Grid
    # shu rasmni ko'rsatadi (CPU ~0, jonli oqimga tegmaydi); kamera ochilsa jonli
    # oqim alohida ishga tushadi. Kesh shu muddatdan eski bo'lsa qayta olinadi.
    # O'CHIQ (xozircha): server yukini minimal tutish uchun. Yoqilganda grid har
    # kameradan davriy rasm oladi (bulut capture / jonli-grab) — stress paytida
    # ortiqcha. Kerak bo'lganda True qilib qaytariladi.
    ezviz_snapshot_enabled: bool = True
    # Har kamera uchun rasm shu muddatda bir marta yangilanadi. Grid uchun 10 daqiqa
    # yetarli — rasm barqaror turadi (tez-tez o'zgarib ko'zga tashlanmaydi) va cloud
    # yuki minimal. (5-15 daqiqa oralig'i maqbul.)
    ezviz_snapshot_interval_seconds: int = 600
    # Fon snapshot-warmer: barcha EZVIZ kameralarni sekin aylanib snapshot'larini
    # OLDINDAN to'ldiradi, shunda grid ochilganda qora kvadrat emas, oxirgi kadr
    # ko'rinadi (foydalanuvchi sahifani ochishini kutmaydi). 0 = o'chirilgan.
    # Har kamera orasidagi pauza (bulutni hammalab yubormaslik uchun).
    ezviz_snapshot_warm_enabled: bool = True
    ezviz_snapshot_warm_gap_seconds: float = 2.0

    # CORS (dev uchun frontend)
    cors_origins: list[str] = ["http://localhost:5173"]
    # CSRF / WebSocket hijacking: cookie (vz_admin) bilan kelgan holat o'zgartiruvchi
    # so'rov va WS ulanishida `Origin` panelning o'z manzili bo'lishi shart (Host /
    # X-Forwarded-Host yoki cors_origins / quyidagi ro'yxat). SameSite=Lax qo'shni
    # subdomen hujumidan himoya qilmaydi. Proksi Host'ni o'zgartirsa — panel manzilini
    # TRUSTED_ORIGINS ga yozing (vergul bilan) yoki vaqtincha CSRF_ORIGIN_CHECK=false.
    csrf_origin_check: bool = True
    trusted_origins: str = ""

    # Frontend build papkasi (production'da backend xizmat qiladi)
    frontend_dist: str = "../frontend/dist"


    # --- Login rate-limit ---
    login_max_attempts: int = 10
    login_window_seconds: int = 300
    # Ilova oldidagi ISHONCHLI reverse-proxy (nginx) soni. 0 = X-Forwarded-For'ga
    # umuman ishonmaymiz (to'g'ridan-to'g'ri ulanish). Proksi ortida bo'lsa
    # (prod) 1 qo'ying — shunda mijoz IP'si XFF'ning OXIRIDAN olinadi (spoofing
    # bo'lmaydi: hujumchi yuborgan soxta qiymatlar chap tomonda qoladi).
    trusted_proxy_count: int = 0

    @property
    def always_warm_ids(self) -> list[str]:
        """`always_warm_camera_ids` ni ro'yxatga aylantiradi (bo'sh/xato qiymatlar tashlanadi).

        Vergulli matn sifatida saqlaymiz — pydantic-settings `list[str]` ni muhitdan
        JSON kutadi, bu esa .env da noqulay va xatoga moyil.
        """
        out = []
        for part in (self.always_warm_camera_ids or "").split(","):
            p = part.strip()
            if p:
                out.append(p)
        return out

    @property
    def runs_api(self) -> bool:
        """Panel/REST va fon vazifalari shu jarayondami (`all` yoki `api`)."""
        return self.app_role.strip().lower() != "media"

    @property
    def runs_media(self) -> bool:
        """Video endpoint'lari shu jarayondami (`all` yoki `media`)."""
        return self.app_role.strip().lower() != "api"

    @property
    def is_production(self) -> bool:
        # Fail-closed: "development"dan boshqa har qanday qiymat (staging, unset
        # typo, bo'sh) production-darajada deb hisoblanadi — sir guardlari ishlaydi.
        return self.environment.strip().lower() != "development"

    @model_validator(mode="after")
    def _harden(self) -> "Settings":
        # Production'da debug va SQL echo o'chiriladi
        if self.is_production:
            self.debug = False
        return self

    def insecure_defaults(self) -> list[str]:
        """Production uchun yaroqsiz/zaif sirlar ro'yxati (bo'sh bo'lsa — xavfsiz)."""
        bad = []
        # JWT secret: ma'lum placeholderlar YOKI juda qisqa/past-entropiyali.
        weak_jwt = {"change-me-in-production", "change-me", "change-me-please", "secret"}
        if self.jwt_secret in weak_jwt or len(self.jwt_secret) < 32:
            bad.append("JWT_SECRET")
        # AES kalit: aniq 64 hex belgi, yaroqli hex, va past-entropiyali emas
        # (all-zero, all-same, "0101..." kabilar rad etiladi).
        key = self.aes_key_hex.strip()
        is_hex = len(key) == 64 and all(c in "0123456789abcdefABCDEF" for c in key)
        if not is_hex or len(set(key.lower())) <= 2:
            bad.append("AES_KEY_HEX")
        if self.admin_password == "admin" or len(self.admin_password) < 8:
            bad.append("ADMIN_PASSWORD")
        if len(self.mediamtx_password) < 16:
            bad.append("MEDIAMTX_PASSWORD")
        return bad


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
