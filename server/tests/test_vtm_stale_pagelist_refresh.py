"""Hisobga YANGI qo'shilgan kamera "OFLAYN" bo'lib qolmasin.

VTM pagelist keshi HISOB bo'yicha, TTL 1 soat, fon isitishi TTL/2 (30 daq) da
yuradi. Hisobga yangi kamera qo'shilsa (yoki uzoq oflayn qurilma bulutga qaytsa)
keshdagi ro'yxatda u YO'Q va `open_cloud_stream` "Could not find VTM resource for
serial ..." beradi. Bu xato OFLAYN qurilma bilan bir xil ko'rinadi, shuning uchun
publisher kamerani oflayn deb chiqib ketardi va keyingi isitishgacha panelda
video umuman chiqmasdi.

Jonli serverda o'lchandi (2026-09-08, bitta yangi kamera): bulut
qurilma ro'yxatida kamera BOR (status=1), keshlangan VTM ro'yxatida esa YO'Q —
keshda 18 resurs, bulutda 19, farq aynan shu kamera. Kesh yangilangach oqim
darhol ochildi (trek H264, 5 sekundda 2.3 MB).

Yechim: oflayn deb hisoblashdan OLDIN ro'yxat BIR MARTA bulutdan yangilanadi.
Kesh O'CHIRILMAYDI (bulut xatosi eski ro'yxatni yo'q qilmasin) va yangi kesh
uchun so'rov TAKRORLANMAYDI (oflayn kamerani bosish bulutni urib qo'ymasin).
"""
import inspect

import pytest

from vizora.sources.ezviz import publish, vtm_cache


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ezviz_vtm_cache_seconds", 3600)
    return tmp_path


def _token_file(cache_dir, key: str) -> str:
    return str(cache_dir / f"{key}.json")


def _fake_cloud(monkeypatch, key: str, fresh, calls: list):
    """`_install_vtm_cache` o'ramiga taqlid: keshdan o'qiydi, bo'lmasa yozadi."""
    import pyezvizapi.cloud_stream as _cs

    def _fetch(_client):
        calls.append(1)
        hit = vtm_cache.load(key, "pagelist")
        if hit is not None:
            return hit
        vtm_cache.store(key, "pagelist", fresh)
        return fresh

    monkeypatch.setattr(_cs, "get_vtm_page_list", _fetch, raising=False)


def test_stale_cache_is_refetched_from_cloud(cache_dir, monkeypatch):
    """Kesh eskirgan bo'lsa — bulutdan olib, YANGI ro'yxat keshga tushsin."""
    key = "acc-stale"
    vtm_cache.store(key, "pagelist", {"resourceInfos": [{"deviceSerial": "AAA0000001"}]})
    # Keshni "eski" qilamiz: floor'dan katta yosh
    _age_out(cache_dir, key)
    fresh = {"resourceInfos": [{"deviceSerial": "AAA0000001"},
                               {"deviceSerial": "BF1916346", "localIndex": "1"}]}
    calls: list = []
    _fake_cloud(monkeypatch, key, fresh, calls)

    assert publish._refresh_pagelist(object(), _token_file(cache_dir, key), "BF1916346")
    assert calls == [1], "bulutga borilmadi (kesh o'qishi chetlab o'tilmagan)"
    assert vtm_cache.load(key, "pagelist") == fresh
    assert vtm_cache.available_channels(key)["BF1916346"] == {1}


def test_fresh_cache_is_not_refetched(cache_dir, monkeypatch):
    """Kesh hozir yangilangan bo'lsa — ro'yxat haqiqatan shunday, so'rov bekor.

    Aks holda oflayn kamerani ketma-ket bosish har bosishda bulutga so'rov
    yuborardi (real bazada 151 kameradan 31 tasi oflayn edi)."""
    key = "acc-fresh"
    vtm_cache.store(key, "pagelist", {"resourceInfos": []})
    calls: list = []
    _fake_cloud(monkeypatch, key, {"resourceInfos": [{"deviceSerial": "X"}]}, calls)

    assert publish._refresh_pagelist(object(), _token_file(cache_dir, key), "X") is False
    assert calls == [], "yangi kesh ustiga bekorga bulut so'rovi ketdi"


def test_cloud_error_keeps_old_cache(cache_dir, monkeypatch):
    """Bulut yiqilsa ESKI ro'yxat joyida qolsin (kesh HISOB bo'yicha!)."""
    key = "acc-boom"
    vtm_cache.store(key, "pagelist", {"eski": True})
    _age_out(cache_dir, key)

    import pyezvizapi.cloud_stream as _cs

    def _boom(_client):
        raise RuntimeError("504 Server Error: Gateway Timeout")

    monkeypatch.setattr(_cs, "get_vtm_page_list", _boom, raising=False)

    assert publish._refresh_pagelist(object(), _token_file(cache_dir, key), "X") is False
    assert vtm_cache.load(key, "pagelist") == {"eski": True}


def test_missing_cache_is_fetched(cache_dir, monkeypatch):
    """Kesh umuman bo'lmasa ham yangilash ishlasin (yosh = None)."""
    key = "acc-none"
    calls: list = []
    _fake_cloud(monkeypatch, key, {"resourceInfos": [{"deviceSerial": "Y"}]}, calls)

    assert publish._refresh_pagelist(object(), _token_file(cache_dir, key), "Y")
    assert calls == [1]


def test_refresh_runs_before_declaring_camera_offline():
    """Publisher "oflayn" xulosasidan OLDIN ro'yxatni yangilashi kerak.

    Manba bo'yicha qulf: bu tartib buzilsa yangi kamera yana 30 daqiqagacha
    "oflayn" bo'lib qoladi."""
    src = inspect.getsource(publish._run_decrypt)
    assert "_refresh_pagelist(client, token_file, serial)" in src, \
        "ro'yxat yangilanmayapti — yangi kamera oflayn ko'rinadi"
    refresh_at = src.index("_refresh_pagelist(client, token_file, serial)")
    exit_at = src.index("if missing_tries >= 2:")
    assert refresh_at < exit_at, "yangilash chiqishdan KEYIN — foydasi yo'q"


def test_refresh_never_invalidates_the_shared_cache():
    """Yangilash keshni O'CHIRMASIN — u HISOB bo'yicha (hamma kamera zarar ko'radi)."""
    code = _code_only(publish._refresh_pagelist)
    assert "invalidate(" not in code
    assert "bypass_read()" in code, "kesh o'qishi chetlab o'tilmasa qayta olish bekor"


def _code_only(fn) -> str:
    """Funksiya manbasi — docstring va izohlarsiz (ular "invalidate" so'zini
    tushuntirish uchun ishlatadi, bu esa manba qulfini yolg'on yiqitardi)."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        node.body = node.body[1:]          # docstring'ni tashlaymiz
    return ast.unparse(node)               # izohlar ham tushib qoladi


def _age_out(cache_dir, key: str) -> None:
    """Kesh yozuvining `ts`ini floor'dan orqaga suradi (eskirgan qiladi)."""
    import json
    p = cache_dir / f"vtm-{key}-pagelist.json"
    blob = json.loads(p.read_text())
    blob["ts"] -= publish._PAGELIST_REFRESH_FLOOR + 10
    p.write_text(json.dumps(blob))
