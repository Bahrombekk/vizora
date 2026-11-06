"""MediaMTX Control API bilan ishlash — kamera uchun oqim path'ini sozlash.

Ikki rejim:
  * transcode=False  -> MediaMTX RTSP'dan to'g'ridan tortadi (sourceOnDemand).
  * transcode=True   -> runOnDemand ffmpeg HEVC/boshqa kodekni H.264 ga o'girib,
                        MediaMTX'ga RTSP orqali publish qiladi.
On-demand: oqim faqat tomoshabin (HLS o'quvchi) bo'lganda boshlanadi.
"""
import logging

import httpx

from vizora.core.config import settings

log = logging.getLogger("vizora.mediamtx")

# ffmpeg konteyner ichida MediaMTX'ning o'ziga publish qiladi.
# -g 50 (25fps'da 2s) -> 2 soniyalik keyframe oralig'i -> qisqa HLS segmentlar.
# -maxrate/-bufsize: VBV bilan bitrate shiftini cheklaymiz. Shift yuqori-rezolyutsiyali
# (2K/4MP) kameralar uchun yetarlicha keng bo'lishi kerak — aks holda x264 QP'ni
# maksimumga ko'tarib (loyqa tasvir) ham sig'dirolmay VBV underflow beradi. Beqaror
# tarmoqqa moslashish endi ABR sub-stream orqali (HD↔SD) bo'ladi, shu sabab bu yerda
# shift faqat "qochib ketgan" bitrate'ni tiyish uchun keng qo'yilgan.
_MAXRATE = "6000k"
_BUFSIZE = "12000k"


# ffmpeg/ffprobe faqat RTSP oqim protokollarini ochsin: file:, http:, concat:, subfile:
# kabilar orqali SSRF va lokal fayl o'qishning oldi olinadi.
FF_PROTOCOLS = "rtsp,rtsps,rtp,srtp,tcp,udp,tls"


def _transcode_cmd(rtsp_url: str) -> str:
    """runOnDemand ffmpeg buyrug'i (MediaMTX KONTEYNERI ichida ishlaydi).

    Enkoder `MEDIAMTX_TRANSCODE_ENCODER` bilan almashtiriladi: GPU'li serverda
    `h264_nvenc` qo'yiladi (mediamtx image'i ham NVIDIA runtime'da bo'lishi kerak),
    aks holda `libx264`. DIQQAT: bu backend konteyneridagi `FFMPEG_HWACCEL` dan
    ALOHIDA — bu ffmpeg boshqa konteynerda (bluenviron/mediamtx:*-ffmpeg) turadi.

    Kalit kadr VAQT bo'yicha (har `ezviz_keyframe_seconds`) — `-g 50` kadr bo'yicha
    edi va 25fps deb taxmin qilardi; kamera 10-15fps bo'lsa GOP 4-5s ga cho'zilib
    HLS segmentlari uzayardi (sovuq start sekin). Vaqt bo'yicha majburlash barcha
    fps'da ~1s segment beradi.
    """
    enc = settings.mediamtx_transcode_encoder
    kf = max(0.5, float(settings.ezviz_keyframe_seconds))
    if enc == "h264_nvenc":
        # -forced-idr 1 MAJBURIY: busiz NVENC majburiy kalit kadrni I (IDR EMAS)
        # qilib beradi va MediaMTX HLS muxeri segment boshlay olmaydi (oqim
        # "ready" bo'lib turadi, lekin playlist yaratilmaydi). [[ffmpeg_hw]] ga q.
        enc_args = (f"-c:v {enc} -preset p1 -tune ull -profile:v high -rc cbr "
                    "-zerolatency 1 -forced-idr 1 -delay 0 -bf 0 -rc-lookahead 0 "
                    "-no-scenecut 1 "
                    f"-b:v {_MAXRATE} -maxrate {_MAXRATE} -bufsize {_MAXRATE}")
    else:
        enc_args = (f"-c:v {enc} -preset {settings.ffmpeg_x264_preset} "
                    "-tune zerolatency -profile:v high -pix_fmt yuv420p "
                    f"-bf 0 -refs 1 -sc_threshold 0 -maxrate {_MAXRATE} -bufsize {_BUFSIZE}")
    return (
        # `nobuffer` QO'YILMAYDI: u probe paytidagi paketlarni (SPS/PPS + birinchi
        # IDR) tashlab yuboradi va oqim boshida KUL RANG tasvir beradi
        # ([[ffmpeg_hw]] / publish._FF_INPUT izohiga qarang). Kechikish uchun
        # `analyzeduration 0` + kichik `probesize` yetarli.
        'ffmpeg -rtsp_transport tcp -flags low_delay '
        '-analyzeduration 0 -probesize 200k -thread_queue_size 1024 '
        f'-protocol_whitelist {FF_PROTOCOLS} -i "{{rtsp}}" '
        f"-an {enc_args} "
        f'-force_key_frames "expr:gte(t,n_forced*{kf:g})" '
        "-f rtsp rtsp://localhost:8554/$MTX_PATH"
    ).format(rtsp=rtsp_url)


def build_path_config(rtsp_url: str, transcode: bool, always_on: bool = False) -> dict:
    """MediaMTX path sozlamasi.

    always_on=True — kamera "doim issiq" (`Camera.keep_warm`): manba tomoshabin
    kutmasdan DOIMIY tortiladi (`sourceOnDemand: false`, transkodda
    `runOnInit` + qayta ishga tushirish), shuning uchun ochilish bir zumda."""
    # Ikkinchi qavat: schema allaqachon tekshiradi, lekin eski/qo'lda yozilgan DB
    # qiymati ham ffmpeg buyrug'iga tushmasin ([[schemas.camera.check_rtsp_url]]).
    from vizora.schemas.camera import check_rtsp_url

    check_rtsp_url(rtsp_url)
    rtsp_url = with_creds(rtsp_url)   # ISUP: MediaMTX o'z path'idan tortadi — login kerak
    if transcode:
        if always_on:
            return {"runOnInit": _transcode_cmd(rtsp_url), "runOnInitRestart": True}
        return {
            "runOnDemand": _transcode_cmd(rtsp_url),
            "runOnDemandRestart": True,
        }
    # `rtspTransport` BERILMASA MediaMTX `automatic` ishlatadi: avval UDP'ni
    # sinaydi, paket kelmasa TCP'ga o'tadi. Jurnalda ko'rindi:
    # "no UDP packets received, switching to TCP" — va bu ~3s BEKOR kutish,
    # ya'ni har RTSP kamera ochilishiga qo'shiladi.
    return {"source": rtsp_url, "sourceOnDemand": not always_on,
            "rtspTransport": settings.mediamtx_rtsp_transport}


def api_auth() -> tuple[str, str] | None:
    """MediaMTX ichki foydalanuvchisi (Basic auth) — API, HLS va WHEP so'rovlari uchun.

    Parol berilmagan bo'lsa None (eski, parolsiz mediamtx.yml)."""
    if settings.mediamtx_user and settings.mediamtx_password:
        return (settings.mediamtx_user, settings.mediamtx_password)
    return None


def _userinfo() -> str:
    from urllib.parse import quote

    a = api_auth()
    return f"{quote(a[0], safe='')}:{quote(a[1], safe='')}@" if a else ""


def rtsp_url(path: str) -> str:
    """MediaMTX'dagi path'ning ichki RTSP manzili — login/parol bilan.

    Publish (EZVIZ publisher), o'qish (WS hub, snapshot) — hammasi shu orqali."""
    return f"rtsp://{_userinfo()}{settings.mediamtx_rtsp_host}:8554/{path}"


def with_creds(url: str) -> str:
    """MediaMTX'ning O'ZIGA ishora qilgan URL'ga ichki login qo'shadi.

    ISUP kameraning saqlangan URL'i `rtsp://mediamtx:8554/isup/...` — MediaMTX
    uni `source` sifatida o'zidan tortadi, snapshot/probe ham shundan o'qiydi; parolsiz
    endi rad etiladi. Bazaga parolli URL yozilmaydi — qo'shish faqat ishlatishda."""
    from urllib.parse import urlsplit

    try:
        u = urlsplit(url)
    except ValueError:
        return url
    if (u.scheme == "rtsp" and not u.username and _userinfo()
            and (u.hostname or "").lower() == settings.mediamtx_rtsp_host.lower()):
        return u._replace(netloc=_userinfo() + u.netloc).geturl()
    return url


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=settings.mediamtx_api_url, timeout=10.0, auth=api_auth()
    )


# `/v3/paths/list` SAHIFALANADI (standart 100 element/sahifa). Bir sahifada
# ko'p element so'rash mumkin, lekin faqat shunga tayanish mo'rt — kamera soni
# o'sganda jimgina yana kesiladi. Shuning uchun `pageCount` bo'yicha AYLANAMIZ.
_PAGE_SIZE = 500
_MAX_PAGES = 50          # xavfsizlik chegarasi (25 000 path)


async def list_paths() -> list[dict]:
    """MediaMTX'dagi HAMMA path — sahifalarni to'liq o'qib.

    NEGA MUHIM: avval faqat BIRINCHI sahifa o'qilardi. 158 kamerada (asosiy +
    `/sub` = 221 path) kameralarning yarmidan ko'pi ro'yxatga tushmasdi, ya'ni
    `_path_ready` ularni HECH QACHON "tayyor" deb ko'rmasdi va har ochilish
    `wait_ready` ning 18s chegarasiga urilib "kamera ochilmadi" bo'lardi.
    O'lchangan: ffmpeg MediaMTX'ga 102 marta publish qilgan, ilova esa faqat
    24 marta "path ready" ko'rgan. 34 kamerada (<100 path) muammo ko'rinmasdi —
    shuning uchun bu FAQAT ko'p kamerada chiqadigan xato.
    """
    out: list[dict] = []
    try:
        async with _client() as c:
            page = 0
            while page < _MAX_PAGES:
                r = await c.get("/v3/paths/list",
                                params={"page": page, "itemsPerPage": _PAGE_SIZE})
                r.raise_for_status()
                d = r.json()
                out.extend(d.get("items") or [])
                if page + 1 >= int(d.get("pageCount") or 1):
                    break
                page += 1
    except httpx.HTTPError:
        return []          # MediaMTX yo'q/xato — bo'sh ro'yxat (chaqiruvchi yiqilmasin)
    return out


async def get_path(name: str) -> dict | None:
    """BITTA path holati (sahifalashdan xoli, O(1)).

    Bitta kameraning tayyorligini bilish uchun butun ro'yxatni tortish shart
    emas — MediaMTX'ning `paths/get` endpointi nomi bo'yicha to'g'ridan beradi
    (path nomidagi `/` ham qabul qilinadi, tekshirildi)."""
    async with _client() as c:
        r = await c.get(f"/v3/paths/get/{name}")
        if r.status_code == 200:
            return r.json()
        return None


async def upsert_path(name: str, rtsp_url: str, transcode: bool, always_on: bool = False) -> None:
    """Path'ni yaratadi yoki yangilaydi. Sozlama o'zgarmagan bo'lsa TEGMAYDI.

    Avval har chaqiruvda o'chirib-qo'shardi — `/stream` va iste'molchi API'si ham
    shuni chaqiradi, ya'ni har qanday API mijoz `/api/v1/cameras/{id}/stream` ni
    takrorlab kameraning BARCHA tomoshabinlarini uzib turishi mumkin edi (DoS)."""
    cfg = build_path_config(rtsp_url, transcode, always_on)
    async with _client() as c:
        g = await c.get(f"/v3/config/paths/get/{name}")
        if g.status_code == 200:
            try:
                cur = g.json()
            except ValueError:
                cur = {}
            if all(cur.get(k) == v for k, v in cfg.items()):
                return
        await c.request("DELETE", f"/v3/config/paths/delete/{name}")  # 404 bo'lsa ham mayli
        r = await c.post(f"/v3/config/paths/add/{name}", json=cfg)
        if r.status_code != 200:
            log.error("MediaMTX path qo'shilmadi (%s): %s", name, r.text)
            r.raise_for_status()
        log.info("MediaMTX path sozlandi: %s (transcode=%s, doimiy=%s)", name, transcode, always_on)


async def ensure_path(name: str) -> None:
    """Manbasiz publishable path yaratadi (EZVIZ publisher shunga push qiladi).

    EZVIZ kameralarda RTSP `source` yo'q — oqimni backend publisher RTSP orqali
    MediaMTX'ga o'zi push qiladi. Bu path shunchaki qabul qiluvchi sifatida turadi.
    """
    async with _client() as c:
        # IDEMPOTENT: mavjud bo'lsa TEGMAYMIZ — aks holda o'chir-qo'sh ulangan
        # publisher'ni uzadi (har /stream chaqiruvida path qayta yaratilmasin).
        g = await c.get(f"/v3/config/paths/get/{name}")
        if g.status_code == 200:
            return
        r = await c.post(f"/v3/config/paths/add/{name}", json={})
        if r.status_code != 200:
            log.error("MediaMTX EZVIZ path qo'shilmadi (%s): %s", name, r.text)
            r.raise_for_status()
        log.info("MediaMTX EZVIZ path tayyor: %s", name)


async def safe_ensure_path(name: str) -> None:
    try:
        await ensure_path(name)
    except httpx.HTTPError as exc:  # noqa: BLE001
        log.warning("MediaMTX ensure_path xato (%s): %s", name, exc)


async def remove_path(name: str) -> None:
    async with _client() as c:
        try:
            await c.request("DELETE", f"/v3/config/paths/delete/{name}")
            log.info("MediaMTX path o'chirildi: %s", name)
        except httpx.HTTPError as exc:  # noqa: BLE001
            log.warning("MediaMTX path o'chirishda xato (%s): %s", name, exc)


async def safe_upsert(name: str, rtsp_url: str, transcode: bool, always_on: bool = False) -> None:
    """Xatoni yutadi — REST javobi MediaMTX tufayli buzilmasin."""
    try:
        await upsert_path(name, rtsp_url, transcode, always_on)
    except httpx.HTTPError as exc:  # noqa: BLE001
        log.warning("MediaMTX upsert xato (%s): %s", name, exc)
    except ValueError as exc:  # yaroqsiz RTSP URL — path sozlanmaydi
        log.warning("MediaMTX path %s: RTSP URL rad etildi — %s", name, exc)


async def apply_rtsp_paths(cam, *, prune_sub: bool = False) -> None:
    """RTSP kamera uchun asosiy va (bo'lsa) sub path'larni sozlaydi.

    Sub path ALOHIDA bayroq (`cam.sub_transcode`) bilan sozlanadi — asosiy oqimning
    `transcode`i bilan EMAS. Kameraning ikkinchi profili deyarli doim H.264 (past
    o'lcham), asosiysi esa HEVC bo'lishi mumkin; sub'ni asosiysi tufayli transkod
    qilish bekorga ikki barobar CPU/GPU yeydi. Sub kodeki aniqlanmaguncha
    (`sub_codec="auto"`) transkodsiz to'g'ridan tortiladi va probe HEVC topsagina
    yoqiladi (`probe.py`).

    prune_sub=True — sub URL o'chirilgan bo'lsa path ham olib tashlanadi (admin
    CRUD uchun). Tomoshabin yo'lida (on-demand /stream) kerak emas: har so'rovda
    ortiqcha DELETE urmaslik uchun.
    """
    from vizora.core import crypto  # kech import — modul yuklanish siklini uzadi

    # "Doim issiq" faqat ASOSIY path'ga: sub (ABR SD) on-demand qoladi.
    if getattr(cam, "keep_warm", False):
        await safe_upsert(cam.stream_path, crypto.decrypt(cam.rtsp_url_enc), cam.transcode, True)
    else:
        await safe_upsert(cam.stream_path, crypto.decrypt(cam.rtsp_url_enc), cam.transcode)
    sub = f"{cam.stream_path}/sub"
    if cam.rtsp_sub_url_enc:
        # Sub'ni ochib bo'lmasa (AES kalit almashgan bo'lishi mumkin) ASOSIY oqimni
        # qurbon qilmaymiz — ABR'siz, bitta sifatda ishlayveradi.
        try:
            sub_rtsp = crypto.decrypt(cam.rtsp_sub_url_enc)
        except Exception as exc:  # noqa: BLE001
            log.warning("Kamera %s sub-stream URL'ini ochib bo'lmadi: %s", cam.stream_path, exc)
            return
        await safe_upsert(sub, sub_rtsp, cam.sub_transcode)
    elif prune_sub:
        await remove_path(sub)
