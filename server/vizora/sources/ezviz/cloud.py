"""pyezvizapi bulut mijozini qurish — publisher, snapshot va VTM keshi uchun UMUMIY.

Ilgari bu `publish.py` (subprocess kirish nuqtasi) ichida yashirin `_make_pyezviz_client`
edi va snapshot/vtm_cache uni o'sha og'ir moduldan import qilardi. Endi alohida:
`make_client()` — token faylidan mijoz, Hik-Connect sozlamalari (clientType=55,
sahifalovchi VTM pagelist, authAddr) va hisob darajasidagi VTM keshi bilan.
"""
from __future__ import annotations

import json
import logging

from vizora.core.config import settings
from vizora.sources.ezviz import vtm_cache

log = logging.getLogger("vizora.ezviz.cloud")


# build_vtm_url'ning ASL (pristine) nusxasi — bir marta saqlanadi (qayta-patch
# qilmaslik uchun). Substream'ni yoqib/o'chirganda shu asosida almashtiramiz.
_orig_build_vtm_url = None


def set_substream(enable: bool) -> None:
    """VTM URL'ini SUBSTREAM (stream=2) yoki ASOSIY (stream=1) oqimga sozlaydi.

    Substream past o'lcham (~D1/CIF) -> bulut/zaif-uplink'da paket yo'qolmaydi ->
    yashil/buzilish bo'lmaydi VA CPU tejaladi. Hamma kamerada substream bo'lmasligi
    mumkin (masalan batareyali) -> publisher fallback bilan asosiyga qaytadi."""
    global _orig_build_vtm_url
    import re as _re
    import pyezvizapi.cloud_stream as _csub
    if _orig_build_vtm_url is None:
        _orig_build_vtm_url = _csub.build_vtm_url  # pristine — bir marta
    if enable:
        def _substream_bvu(*a, **kw):
            return _re.sub(r"(?<=[?&])stream=1(?=&|$)", "stream=2",
                           _orig_build_vtm_url(*a, **kw))
        _csub.build_vtm_url = _substream_bvu
    else:
        _csub.build_vtm_url = _orig_build_vtm_url


def make_client(token_file: str, provider: str = "ezviz",
                         substream: bool | None = None):
    from pyezvizapi.client import EzvizClient as PyEzvizClient

    with open(token_file, encoding="utf-8") as f:
        token = json.load(f)
    client = PyEzvizClient(token.get("username"), None, token.get("api_url"), token=token)

    if substream is None:
        substream = settings.ezviz_prefer_substream
    set_substream(substream)

    # EZVIZ uchun kutubxona standart holida ishlaydi (sinovdan o'tgan). Faqat
    # Hik-Connect uchun qo'shimcha sozlamalar: clientType=55, to'liq sahifalovchi
    # VTM pagelist (ko'p NVR hisobida kameralar topilishi uchun) va authAddr derive.
    if provider == "hik":
        import re
        import pyezvizapi.cloud_stream as _cs
        import requests as _rq

        client_type = token.get("client_type") or "55"
        try:
            client._session.headers.update({"clientType": client_type, "lang": "en-US"})
        except Exception:  # noqa: BLE001
            pass

        def _paged_vtm_page_list(_client):
            tok = getattr(_client, "_token", {}) or {}
            api = tok.get("api_url")
            sess = _rq.Session()
            sess.headers.update({
                "clientType": client_type, "lang": "en-US",
                "featureCode": "1fc28fa018178a1cd1c091b13b2f9f02",
                "sessionId": str(tok.get("session_id")),
            })
            base = None
            merged_res, merged_vtm, offset = [], {}, 0
            _MAX_PAGES = 40
            hit_cap = True
            for _ in range(_MAX_PAGES):
                r = sess.get(
                    f"https://{api}/v3/userdevices/v1/resources/pagelist",
                    params={"filter": "VTM", "groupId": -1, "limit": 50, "offset": offset},
                    timeout=25,
                )
                r.raise_for_status()
                try:
                    pl = r.json()
                except ValueError as exc:
                    raise OSError(f"VTM pagelist JSON emas (HTTP {r.status_code})") from exc
                if base is None:
                    base = pl
                res = pl.get("resourceInfos") or []
                merged_res.extend(res)
                vtm = pl.get("VTM")
                if isinstance(vtm, dict):
                    merged_vtm.update(vtm)
                page = pl.get("page") or {}
                if not res or not page.get("hasNext"):
                    hit_cap = False
                    break
                offset += len(res)
            if hit_cap:
                log.warning("VTM pagelist %d sahifa chegarasiga yetdi — "
                            "ba'zi NVR'lar tushib qolishi mumkin", _MAX_PAGES)
            if base is not None:
                base["resourceInfos"] = merged_res
                base["VTM"] = merged_vtm
            return base

        # DIQQAT: buni `_cs.get_vtm_page_list` ga GLOBAL yozib qo'ymaymiz — bir
        # jarayonda bir necha hisob bo'lsa (fon isitish sikli) Hik sahifalovchisi
        # keyingi EZVIZ hisobiga ham qo'llanib ketardi. Mijozga bog'laymiz.
        client._cs_pagelist_impl = _paged_vtm_page_list

        su = token.get("service_urls") or {}
        if not su.get("authAddr") or "null" in str(su.get("authAddr")).lower():
            m = re.match(r"apii([a-z]+)\.", str(token.get("api_url", "")))
            if m:
                client._token.setdefault("service_urls", {})["authAddr"] = \
                    f"https://{m.group(1)}auth.ezvizlife.com"

    # Kesh KIMGA tegishli ekani mijozning o'zida turadi — o'ram uni shundan
    # o'qiydi (global closure emas). Aks holda hisoblar bir-birining keshini
    # o'qib qo'yardi (pastdagi [[install_vtm_cache]] izohiga qarang).
    client._cs_account_key = vtm_cache.account_key(token_file)
    install_vtm_cache()
    return client


def install_vtm_cache() -> None:
    """`get_vtm_page_list` va `get_vtdu_token_v2` ni HISOB bo'yicha fayl keshi bilan o'raydi.

    Ikkala so'rov ham kameraga emas, hisobga tegishli — bir hisobdagi hamma kamera
    uchun javob bir xil. Keshsiz har kamera ochilishida ~6s bulutda bekorga ketardi
    (o'lchangan: pagelist 4.3s + vtdu 1.8s). Publisher'lar alohida jarayon bo'lgani
    uchun kesh fayl orqali (jarayonlararo) bo'ladi.

    BIR MARTA o'rnatiladi va hisob kalitini MIJOZDAN (`_cs_account_key`) o'qiydi.
    Ilgari o'ram har `make_client` chaqiruvida QAYTA qo'yilar va kalitni
    closure'dan olardi. Bir jarayonda bir necha hisob bo'lganda (fon VTM isitish
    sikli barcha hisoblarni ketma-ket aylanadi) o'ramlar ZANJIRLANARDI:

        A: inner=asl,   _cs.get = W_A(kalit=A)
        B: inner=W_A,   _cs.get = W_B(kalit=B)
        B so'raydi -> B keshi bo'sh -> inner=W_A -> A keshi TO'LA -> A ning
        qurilmalari qaytadi -> W_B ularni B keshiga YOZADI.

    Natijasi jonli serverda o'lchandi: 4 ta hisobning 3 tasida bir xil qurilma
    ro'yxati (74 qurilma, data-md5 bir xil) yotardi. O'z hisobida bo'lmagan
    kameralar uchun publisher `Could not find VTM resource for serial ...` berib
    "kamera OFLAYN" deb chiqardi — panelda video umuman ko'rinmasdi, holbuki
    kamera onlayn va mobil ilovada ishlardi. Hik sahifalovchisi ham xuddi shunday
    global qo'yilardi va keyingi EZVIZ hisobiga qo'llanib ketardi.
    """
    import pyezvizapi.cloud_stream as _cs

    if getattr(_cs.get_vtm_page_list, "_cs_wrapped", False):
        return                                   # allaqachon o'rnatilgan

    inner_pagelist = _cs.get_vtm_page_list       # ASL (o'ralmagan) funksiyalar
    inner_vtdu = _cs.get_vtdu_token_v2

    def _cached_pagelist(_client):
        key = getattr(_client, "_cs_account_key", None)
        if key:
            hit = vtm_cache.load(key, "pagelist")
            if hit is not None:
                log.info("[vaqt] VTM pagelist KESHDAN (hisob=%s)", key)
                return hit
        # Hik hisobida sahifalovchi MIJOZGA bog'langan bo'ladi (global emas).
        fetch = getattr(_client, "_cs_pagelist_impl", None) or inner_pagelist
        data = fetch(_client)
        if key:
            vtm_cache.store(key, "pagelist", data)
        return data

    def _cached_vtdu(_client, *a, **kw):
        key = getattr(_client, "_cs_account_key", None)
        if key:
            hit = vtm_cache.load(key, "vtdu")
            if hit is not None:
                log.info("[vaqt] VTDU token KESHDAN (hisob=%s)", key)
                return hit
        data = inner_vtdu(_client, *a, **kw)
        if key:
            vtm_cache.store(key, "vtdu", data)
        return data

    _cached_pagelist._cs_wrapped = True
    _cached_vtdu._cs_wrapped = True
    _cs.get_vtm_page_list = _cached_pagelist
    _cs.get_vtdu_token_v2 = _cached_vtdu
