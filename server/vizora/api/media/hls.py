"""HLS proxy — frontend bilan bir xil origin + autentifikatsiya.

Frontend/iste'molchi `/hls/cam/<id>/index.m3u8?token=<stream_token>` so'raydi.
Ruxsat manbalari (tartib bo'yicha):
  1. `vz_stream` cookie (oldingi yaroqli so'rovdan o'rnatilgan, kameraga bog'langan),
  2. `?token=` query (stream token yoki admin JWT),
  3. `Authorization: Bearer` (stream token yoki admin JWT).
Token kameraga bog'langan: bir kamera tokeni boshqa kamerani ko'rsatmaydi.
"""
import asyncio
import re

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from vizora.core.config import settings
from vizora.api.media.auth import STREAM_COOKIE, UUID_RE, authorize_http
from vizora.media import mediamtx
from vizora.media.stream import STREAM_EXP_MINUTES, make_stream_token

router = APIRouter(tags=["stream"])

_HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "keep-alive", "set-cookie", "location"}

# To'liq HLS yo'lini QAT'IY tekshiramiz (path-traversal'ga qarshi):
#   cam/<uuid>[/sub]/<fayl>
# Kamera id — UUID (greedy-prefix moslashuv yo'q), ixtiyoriy bitta `sub` darajasi,
# oxirida bitta fayl segmenti (faqat xavfsiz belgilar — `/` va `..` yo'q).
# Bu yagona ruxsat etilgan shakl; boshqasi rad etiladi.
_HLS_PATH_RE = re.compile(
    rf"^(cam/{UUID_RE})(/sub)?/([A-Za-z0-9_.-]+)$"
)
_M3U8 = "application/vnd.apple.mpegurl"

# ABR master playlist variantlari. BANDWIDTH hls.js'ga sifat tartibini bildiradi:
# boshlang'ich daraja default tahmin (~800kbps) bo'yicha tanlanadi va keyin
# tarmoq o'tkazuvchanligiga qarab avto-moslashadi (HD ↔ SD).
# Qiymatlar publisher enkoder bitrate'idan olinadi (config) — mos bo'lishi shart,
# aks holda hls.js noto'g'ri darajani tanlaydi.
_HD_BANDWIDTH = settings.ezviz_hd_bitrate_kbps * 1000
_SD_BANDWIDTH = settings.ezviz_sd_bitrate_kbps * 1000


def _parse_hls_path(path: str) -> tuple[str, str] | None:
    """`/hls/<path>` ni QAT'IY tekshirib (cam_path, mtx_path) qaytaradi.

    cam_path — autentifikatsiya/cookie/sessiya uchun kamera path'i ("cam/<id>").
    mtx_path — MediaMTX'ga uzatiladigan, FAQAT tekshirilgan qismlardan QAYTA
    QURILGAN yo'l (xom mijoz yo'li EMAS). Shakl mos kelmasa None — bu `..`,
    qo'shimcha `/`, yoki boshqa kameraga sakrashni butunlay bloklaydi.
    """
    m = _HLS_PATH_RE.match(path)
    if not m:
        return None
    cam_path, sub, fname = m.group(1), m.group(2) or "", m.group(3)
    if fname in (".", ".."):  # belgilar sinfi `..` ga ruxsat berardi — bloklaymiz
        return None
    return cam_path, f"{cam_path}{sub}/{fname}"


# --- MediaMTX HLS sessiyasini SERVER tomonda boshqarish ---
# MediaMTX har HLS oqimga `?session=<uuid>` beradi (index.m3u8 → cookieCheck orqali)
# va variant playlist/segmentlar shu sessiyaga bog'lanadi. Sessiya muxer reset
# bo'lganda (EZVIZ publisher uzilib-ulanganda) o'ladi → eskirgan sessiya bilan
# so'rov 401 beradi → kamera qotadi. Yechim: sessiyani PROXY o'zi ushlab turadi,
# brauzer so'roviga o'zining amaldagi sessiyasini qo'yadi, 401 bo'lsa jimgina
# yangi sessiya olib qayta uradi. Shunday qilib 401 brauzerga umuman yetmaydi.
_mtx_sessions: dict[str, str] = {}          # stream_path -> mediamtx session id
_sess_locks: dict[str, asyncio.Lock] = {}
_SESSION_RE = re.compile(r"session=([0-9a-fA-F-]{8,})")

# MediaMTX'ning MEDIA playlist fayl nomi HLS variantiga qarab O'ZGARADI:
#   mpegts     -> main_stream.m3u8
#   lowLatency -> video1_stream.m3u8   (trek nomiga bog'liq)
# Avval `main_stream.m3u8` QATTIQ yozilgan edi va LL-HLS'ga o'tganda ABR master
# 404 berardi. Endi nom MediaMTX'ning index.m3u8'idan O'QILADI va keshlanadi.
_mtx_variants: dict[str, str] = {}          # mtx_dir -> "<nom>.m3u8"
_VARIANT_RE = re.compile(r"^([A-Za-z0-9_.-]+\.m3u8)", re.MULTILINE)
_DEFAULT_VARIANT = "main_stream.m3u8"

# LL-HLS "blocking playlist" parametrlari: pleyer keyingi part tayyor bo'lishini
# SERVERDA kutadi (tight-poll qilmaydi). Bularni upstream'ga UZATISH SHART, aks
# holda past kechikish ishlamaydi. Mijoz query'sining qolgani (token) uzatilmaydi.
_LL_PARAMS = ("_HLS_msn", "_HLS_part", "_HLS_skip")


def _sess_lock(stream_path: str) -> asyncio.Lock:
    lk = _sess_locks.get(stream_path)
    if lk is None:
        lk = _sess_locks[stream_path] = asyncio.Lock()
    return lk


async def _obtain_session(stream_path: str) -> str | None:
    """MediaMTX'dan yangi HLS sessiya oladi (index.m3u8 + cookieCheck handshake).

    MediaMTX'ga backendning ichki logini bilan ([[mediamtx.api_auth]]); mijoz
    ruxsati bu yerga kelishdan oldin tekshirilgan.
    """
    url = f"{settings.mediamtx_hls_url}/{stream_path}/index.m3u8"
    # Muxer SOVUQ bo'lsa (ayniqsa SD/sub path birinchi so'ralganda) index.m3u8 darrov
    # 200 bermaydi — MediaMTX muxerni endi yaratadi. Bir necha marta qisqa kutib
    # qayta urinamiz: shunda sessiya olinadi va 401 brauzerga yetmaydi.
    try:
        async with httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=15.0,
                                     follow_redirects=True) as c:
            # Ko'proq, ammo QISQAROQ urinish: umumiy sabr o'sha-o'sha (~2.4s), lekin
            # muxer tayyor bo'lgan zahoti ilib olamiz (oldin 0.6s qadam bilan
            # o'rtacha 0.3s bekorga ketardi — sovuq startda bu sezilarli).
            for attempt in range(8):
                r = await c.get(url)
                if r.status_code == 200:
                    v = _VARIANT_RE.search(r.text)
                    if v:
                        _mtx_variants[stream_path] = v.group(1)
                    m = _SESSION_RE.search(r.text)
                    return m.group(1) if m else None
                if attempt < 7:
                    await asyncio.sleep(0.3)  # muxer isishini kutamiz
            return None
    except httpx.HTTPError:
        return None


async def _ensure_session(stream_path: str, *, force: bool = False) -> str | None:
    async with _sess_lock(stream_path):
        if not force and stream_path in _mtx_sessions:
            return _mtx_sessions[stream_path]
        s = await _obtain_session(stream_path)
        if s:
            _mtx_sessions[stream_path] = s
        else:
            _mtx_sessions.pop(stream_path, None)
        return s


async def _variant_name(mtx_dir: str) -> str:
    """Shu MediaMTX path'ining media playlist fayl nomi (keshlanadi).

    Nom variantga bog'liq (`main_stream.m3u8` / `video1_stream.m3u8`), shuning
    uchun taxmin qilmaymiz — index.m3u8'dan o'qiymiz. Olib bo'lmasa eski
    (mpegts) nom bilan urinib ko'ramiz."""
    name = _mtx_variants.get(mtx_dir)
    if name:
        return name
    await _ensure_session(mtx_dir)      # index.m3u8 o'qiladi -> nom keshlanadi
    return _mtx_variants.get(mtx_dir, _DEFAULT_VARIANT)


def _master_playlist(token: str, hd_variant: str, sd_variant: str) -> str:
    """ABR master.m3u8 sintezi — HD + SD variantlari, TO'G'RIDAN media playlist'ga.

    MUHIM: MediaMTX'ning index.m3u8'i o'zi master playlist. Agar variant sifatida
    index.m3u8 bersak, master→master bo'lib qoladi va hls.js bunday ikki bosqichli
    masterni qo'llamaydi (oqim 0:00 da qotadi). Shuning uchun to'g'ridan MEDIA
    playlist'ga ishora qilamiz. Fayl nomi HLS variantiga bog'liq
    (`main_stream.m3u8` / `video1_stream.m3u8`) — [[_variant_name]] aniqlaydi:
      <hd_variant>      -> /hls/cam/<id>/<hd_variant>       (HD, path cam/<id>)
      sub/<sd_variant>  -> /hls/cam/<id>/sub/<sd_variant>   (SD, path cam/<id>/sub)
    Token bo'lsa, har variant so'roviga qo'shamiz (segmentlar cookie bilan ketadi).
    """
    q = f"?token={token}" if token else ""
    return (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH={_HD_BANDWIDTH},NAME="HD"\n'
        f"{hd_variant}{q}\n"
        f'#EXT-X-STREAM-INF:BANDWIDTH={_SD_BANDWIDTH},NAME="SD"\n'
        f"sub/{sd_variant}{q}\n"
    )


_URI_ATTR_RE = re.compile(rb'URI="([^"]*)"')


def _uri_with_token(u: bytes, tok: bytes) -> bytes:
    if b"token=" in u:
        return u
    sep = b"&" if b"?" in u else b"?"
    return u + sep + b"token=" + tok


def _inject_playlist_token(body: bytes, tok: str) -> bytes:
    """Media playlist'dagi segment/part/init URI'lariga `?token=` qo'shadi.

    NEGA: segmentlar/init (EXT-X-MAP)/partlar odatda `vz_stream` cookie bilan
    avtorizatsiya qilinadi, lekin cookie BOSHQA origin'ga yuborilmaydi. Shuning
    uchun tashqi integrator (o'z domenidan) master + variant playlist'ni token
    bilan ololadi-yu, segment/init so'rovi token'siz kelib 401 ("Oqimga ruxsat
    yo'q") bo'lardi. Token URL'da bo'lsa — cross-origin ham ishlaydi. Token shu
    kameraga (`cam/<id>`) bog'langan, ya'ni HD ham, `/sub` ham bir token bilan
    ochiladi. Har media URI (satr) va har `URI="..."` atributi (EXT-X-MAP/PART/
    PRELOAD-HINT/RENDITION-REPORT) yangilanadi; satr oxiridagi CR normallashtiriladi.
    """
    t = tok.encode()
    out: list[bytes] = []
    for line in body.split(b"\n"):
        s = line.rstrip(b"\r")
        if not s:
            out.append(s)
        elif s.startswith(b"#"):
            if b'URI="' in s:
                s = _URI_ATTR_RE.sub(
                    lambda m: b'URI="' + _uri_with_token(m.group(1), t) + b'"', s)
            out.append(s)
        else:
            out.append(_uri_with_token(s, t))
    return b"\n".join(out)


def _apply_cookie(resp: Response, set_cookie: str, stream_path: str | None) -> None:
    if set_cookie and stream_path:
        # Cookie faqat shu kamera path'iga (bir nechta kamera bir vaqtda
        # ko'rilganda cookie klashi bo'lmasligi uchun).
        resp.set_cookie(
            STREAM_COOKIE, set_cookie, max_age=STREAM_EXP_MINUTES * 60,
            httponly=True, samesite="lax", secure=settings.is_production,
            path=f"/hls/{stream_path}",
        )


@router.get("/hls/{path:path}")
async def hls_proxy(path: str, request: Request):
    parsed = _parse_hls_path(path)
    if parsed is None:
        # Yaroqsiz/xavfli yo'l — autentifikatsiyagacha rad etamiz.
        return JSONResponse({"detail": "Topilmadi"}, status_code=404)
    stream_path, mtx_path = parsed

    grant = await authorize_http(request, stream_path)
    if grant is None:
        return JSONResponse({"detail": "Oqimga ruxsat yo'q"}, status_code=401)
    set_cookie = grant.cookie

    # ABR master playlist — MediaMTX'da yo'q, backend sintez qiladi.
    if path.endswith("/master.m3u8"):
        # Variant URL'lariga ruxsat bergan STREAM tokenning o'zi qo'yiladi — yangisini
        # mint qilish muddatni uzaytirardi (token cheksiz yangilanardi, [[StreamGrant]]).
        # Admin JWT bilan kirilgan bo'lsa uni playlist tanasiga qo'ymaymiz — mint qilamiz.
        variant_token = grant.token or make_stream_token(stream_path, grant.owner)[0]
        hd_v = await _variant_name(stream_path)
        sd_v = await _variant_name(f"{stream_path}/sub")
        resp = Response(
            _master_playlist(variant_token, hd_v, sd_v),
            media_type=_M3U8,
            headers={"Cache-Control": "no-cache"},
        )
        _apply_cookie(resp, set_cookie, stream_path)
        return resp

    # Upstream URL FAQAT tekshirilgan mtx_path'dan quriladi (xom mijoz yo'lidan emas).
    url = f"{settings.mediamtx_hls_url}/{mtx_path}"
    is_index = path.endswith("/index.m3u8")
    is_m3u8 = path.endswith(".m3u8")
    # Sessiya VARIANT path'i bo'yicha boshqariladi: HD = cam/<id>, SD = cam/<id>/sub.
    # ABR'da HD va SD alohida MediaMTX path'lari — alohida sessiya kerak (aks holda
    # bir sessiya ikkisiga ham qo'yilib, biri 401 bo'lardi). Fayl nomini olib
    # tashlab katalogni olamiz (segmentlar ham shu katalogga tushadi).
    mtx_dir = mtx_path.rsplit("/", 1)[0]

    async def _send(inject_session: str | None):
        # follow_redirects: MediaMTX 'cookieCheck' redirectini server tomonda kuzatamiz.
        # MUHIM: MediaMTX HLS sessiyasi `?session=` BILAN BIRGA `cookieCheck=1` cookie'ni
        # talab qiladi (index.m3u8 handshake'da o'rnatiladi). Cookie bo'lmasa variant/
        # segment so'rovlari 401 beradi (ayniqsa SD/sub muxer). Shuning uchun har
        # upstream so'rovga cookieCheck=1 ni qo'shamiz.
        client = httpx.AsyncClient(auth=mediamtx.api_auth(), timeout=30.0,
                                   follow_redirects=True, cookies={"cookieCheck": "1"})
        try:
            # MediaMTX'ga mijoz query'sini UZATMAYMIZ (token sirqishi/ixtiyoriy nazorat
            # paramlarini oldini olish). Sessiyani o'zimiz boshqaramiz.
            params: dict[str, str] = {}
            if inject_session and stream_path:
                params["session"] = inject_session
            # LL-HLS blocking so'rovi: pleyer "keyingi part"ni kutadi. Bu
            # parametrlarsiz MediaMTX playlistni DARHOL qaytaradi va past
            # kechikish yo'qoladi (pleyer bo'sh aylanib qoladi).
            for k in _LL_PARAMS:
                v = request.query_params.get(k)
                if v is not None:
                    params[k] = v
            req = client.build_request("GET", url, params=params)
            up = await client.send(req, stream=True)
            return client, up
        except BaseException:
            # send muvaffaqiyatsiz bo'lsa client ochiq qolib leak bo'lmasin.
            await client.aclose()
            raise

    # index.m3u8 — MediaMTX yangi sessiya generatsiya qiladi (sessiya qo'ymaymiz).
    # Boshqalari (variant playlist + segmentlar) — boshqariladigan sessiyani qo'yamiz.
    inject = None if (is_index or not stream_path) else await _ensure_session(mtx_dir)
    client, upstream = await _send(inject)

    # Sessiya o'lgan bo'lsa (401) — yangi sessiya olib BIR marta jimgina qayta uramiz.
    if upstream.status_code == 401 and stream_path and not is_index:
        await _close(upstream, client)
        inject = await _ensure_session(mtx_dir, force=True)
        client, upstream = await _send(inject)

    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    headers["Cache-Control"] = "no-cache"

    # m3u8 (kichik matn) — to'liq o'qib, sessiyani keshlaymiz (index yangi sessiya bergan
    # bo'lsa keyingi so'rovlar uchun). Segmentlar — oqim (stream) bilan uzatiladi.
    if is_m3u8:
        body = await upstream.aread()
        await _close(upstream, client)
        if upstream.status_code == 200 and stream_path:
            m = _SESSION_RE.search(body.decode(errors="ignore"))
            if m:
                _mtx_sessions[mtx_dir] = m.group(1)
            # Cross-origin: segment/part/init URI'lariga stream token qo'shamiz.
            # Token'ni so'rovni avtorizatsiya qilgan stream tokendan olamiz (query,
            # cookie yoki Bearer) — bo'lmasa (admin JWT) shu path uchun mint qilamiz.
            tok = grant.token or make_stream_token(stream_path, grant.owner)[0]
            body = _inject_playlist_token(body, tok)
        ct = headers.pop("content-type", None) or _M3U8
        resp = Response(content=body, status_code=upstream.status_code,
                        headers=headers, media_type=ct)
        _apply_cookie(resp, set_cookie, stream_path)
        return resp

    resp = StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=headers,
        background=BackgroundTask(_close, upstream, client),
    )
    _apply_cookie(resp, set_cookie, stream_path)
    return resp


async def _close(upstream: httpx.Response, client: httpx.AsyncClient) -> None:
    await upstream.aclose()
    await client.aclose()
