"""HISOB darajasidagi bulut metama'lumotlarini fayl keshi (sovuq startni tezlatish).

Muammo: `open_cloud_stream` har kamera ochilishida ikkita bulut so'rovini qiladi —
`get_vtm_page_list` (Hik'da KO'P SAHIFALI, o'lchangan: ~4.3s / 236 resurs) va
`get_vtdu_token_v2` (~1.8s). Ikkalasi ham KAMERAGA emas, HISOBGA tegishli: bir
hisobdagi 24 ta kamera uchun javob bir xil. Ya'ni har ochilishda ~6.1s bekorga
sarflanardi, va 200 kamerali tizimda bu bulut rate-limit'iga ham urib berardi.

Yechim: natijani hisob bo'yicha faylga keshlaymiz (token fayllari yonida). Har
publisher alohida subprocess bo'lgani uchun kesh JARAYONLARARO bo'lishi shart —
shuning uchun xotira emas, fayl. Birinchi ochilish keshni to'ldiradi, keyingilari
~0.1s da o'qiydi.

Kesh eskirsa yoki oqim ochilmasa — chaqiruvchi `invalidate()` qilib qayta oladi
(VTDU tokeni muddatidan oldin bekor bo'lishi mumkin).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
from typing import Any

from vizora.core.config import settings

log = logging.getLogger("vizora.ezviz.vtm_cache")


def _path(account_key: str, kind: str) -> str:
    safe = "".join(c for c in account_key if c.isalnum() or c in "-_")
    return os.path.join(settings.ezviz_token_dir, f"vtm-{safe}-{kind}.json")


def account_key(token_file: str) -> str:
    """Token fayl nomidan hisob kalitini oladi (`<account_id>.json` -> `<account_id>`)."""
    return os.path.splitext(os.path.basename(token_file))[0]


_tls = threading.local()


@contextlib.contextmanager
def bypass_read():
    """Shu THREAD ichida [[load]] doim None qaytaradi (kesh O'QILMAYDI).

    Nega: [[warm]] keshni YANGILASHI kerak, ya'ni bulutga borishi shart. Lekin u
    `cloud.install_vtm_cache` o'rami orqali chaqiradi, o'ram esa hali yaroqli
    keshni qaytarib yuborardi — isitish bekor ketardi. Ilgari buni `invalidate()`
    bilan hal qilingan edi: kesh OLDIN o'chirilardi, keyin bulutdan olinardi. Bulut
    xato bersa (o'lchangan: 504 Gateway Timeout) kesh BO'SH qolardi va o'sha
    hisobdagi har bir kamera keyingi isitishgacha ~6s ortiq ochilardi.

    Thread-local: `warm` `asyncio.to_thread` da ishlaydi, shuning uchun probe'ning
    `available_channels()` kabi boshqa oqumdagi o'quvchilari ta'sirlanmaydi."""
    prev = getattr(_tls, "bypass", False)
    _tls.bypass = True
    try:
        yield
    finally:
        _tls.bypass = prev


def load(account_key: str, kind: str) -> Any | None:
    """Kesh yangi bo'lsa qiymatni, aks holda None qaytaradi."""
    if getattr(_tls, "bypass", False):
        return None                       # [[bypass_read]] — majburan bulutdan
    p = _path(account_key, kind)
    try:
        with open(p, encoding="utf-8") as f:
            blob = json.load(f)
        age = time.time() - float(blob.get("ts") or 0)
        if age > settings.ezviz_vtm_cache_seconds:
            return None
        return blob.get("data")
    except (OSError, ValueError, TypeError):
        return None


def store(account_key: str, kind: str, data: Any) -> None:
    """Keshni ATOMAR yozadi (bir vaqtda bir necha publisher yozishi mumkin)."""
    p = _path(account_key, kind)
    try:
        os.makedirs(settings.ezviz_token_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=settings.ezviz_token_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "data": data}, f)
            os.replace(tmp, p)  # atomar — yarim yozilgan fayl o'qilmaydi
        except BaseException:
            try:
                os.unlink(tmp)  # yarim yozilgan tmp qolib ketmasin
            except OSError:
                pass
            raise
    except OSError as exc:
        log.warning("VTM keshini yozib bo'lmadi (%s/%s): %s", account_key, kind, exc)


def invalidate(account_key: str, kind: str | None = None) -> None:
    """Keshni o'chiradi — oqim ochilmasa (token eskirgan bo'lsa) qayta olinadi."""
    kinds = [kind] if kind else ["pagelist", "vtdu"]
    for k in kinds:
        try:
            os.unlink(_path(account_key, k))
        except OSError:
            pass


def available_channels(account_key: str) -> dict[str, set[int]] | None:
    """Keshlangan pagelist'dan {serial: {mavjud kanallar}} chiqaradi.

    Kamera holatini ANIQ aniqlash uchun: bulut qurilma (NVR) online bo'lsa ham,
    unda MAVJUD BO'LMAGAN kanal oqim bermaydi. Probe shu bilan "online, lekin
    hech qachon ochilmaydi" holatini to'g'ri "offline" deb belgilaydi.

    Kesh yo'q/eskirgan bo'lsa None — chaqiruvchi holatni O'ZGARTIRMASLIGI kerak
    (noto'g'ri offline bermaslik uchun). Kanal yozuvi topilmagan seriya bo'sh
    to'plam oladi — bir kanalli qurilmalar (channelNo'siz) shunday, ular
    darvozaga TUSHMAYDI."""
    data = load(account_key, "pagelist")
    if data is None:
        return None
    out: dict[str, set[int]] = {}

    def rec(x: Any) -> None:
        if isinstance(x, dict):
            ser = x.get("deviceSerial") or x.get("serial")
            ch = x.get("channelNo")
            if ch is None:
                ch = x.get("localIndex")
            if ch is None:
                ch = x.get("channel")
            if isinstance(ser, str):
                out.setdefault(ser, set())
                if ch is not None:
                    try:
                        out[ser].add(int(ch))
                    except (TypeError, ValueError):
                        pass
            for v in x.values():
                rec(v)
        elif isinstance(x, list):
            for i in x:
                rec(i)

    rec(data)
    return out


def channel_missing(account_key: str, serial: str, channel: int) -> bool:
    """Shu (serial, channel) bulut resurslarida YO'Qmi? (kesh noaniq bo'lsa False).

    False qaytadi agar: kesh yo'q, seriya ro'yxatда yo'q, yoki seriyaning kanal
    to'plami bo'sh (bir kanalli qurilma) — hech biri "yo'q" degan ISHONCHLI
    dalil emas. True faqat: seriya bor, kanal to'plami bo'sh EMAS, kanal unda
    yo'q — ya'ni qurilma bir necha kanalli va aynan shu kanal mavjud emas."""
    chans = available_channels(account_key)
    if not chans:
        return False
    have = chans.get(serial)
    if not have:                       # seriya yo'q yoki kanal to'plami bo'sh
        return False
    # Kanal 0 ko'pincha "qurilmaning o'zi" — uni sanamaymiz
    real = {c for c in have if c != 0}
    if not real:
        return False
    return channel not in have and channel not in real


def age(account_key: str, kind: str) -> float | None:
    """Kesh yoshi (sekund) yoki None (yo'q)."""
    try:
        with open(_path(account_key, kind), encoding="utf-8") as f:
            blob = json.load(f)
        return time.time() - float(blob.get("ts") or 0)
    except (OSError, ValueError, TypeError):
        return None


def warm(token_file: str, provider: str = "ezviz") -> bool:
    """Qurilma ro'yxatini (pagelist) OLDINDAN olib keshga yozadi.

    Nega: TTL tugagach keyingi kamera ochilishi ~4-6s metama'lumot so'roviga
    tushardi (o'lchandi: issiq 0.24s, sovuq 5.69s). Real foydalanishda kameralar
    doim ochilib turmaydi, ya'ni kesh KO'PINCHA sovuq bo'lardi — aynan shu
    "sekin ochilyabdi" ning katta qismi. Fonda yangilab turish 2 ta bulut
    so'rovi/hisob narxida buni butunlay yo'q qiladi.

    Bu funksiya BLOKLAYDI (tarmoq) — `asyncio.to_thread` bilan chaqiring.
    """
    import pyezvizapi.cloud_stream as _cs

    from vizora.sources.ezviz.cloud import make_client

    key = account_key(token_file)
    try:
        # make_client kesh o'ramini VA Hik sahifalash patch'ini o'rnatadi,
        # shuning uchun quyidagi chaqiruv keshga o'zi yozadi.
        # [[bypass_read]]: o'ram eski (hali yaroqli) qiymatni qaytarmasin — bulutga
        # borsin. Kesh FAQAT muvaffaqiyatda almashadi; bulut xatosi eski keshni
        # yo'q qilmaydi (ilgari `invalidate()` qilinardi va 504 kesh'ni bo'shatardi).
        with bypass_read():
            client = make_client(token_file, provider, substream=False)
            _cs.get_vtm_page_list(client)
        return True
    except Exception as exc:  # noqa: BLE001 — isitish "best effort"
        log.warning("VTM keshini isitib bo'lmadi (hisob=%s): %s", key, exc)
        return False

