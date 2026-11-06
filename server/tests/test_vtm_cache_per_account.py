"""VTM keshi HISOBLAR ORASIDA oqib ketmasin — "kamera video ko'rsatmayabdi".

Jonli serverda topildi (2026-09-05): 4 ta EZVIZ hisobining 3 tasida AYNAN bir xil
qurilma ro'yxati yotardi (74 qurilma, data-md5 bir xil). batareyali kameralar hisobidagi 19 ta
kameraning birontasi ham o'z keshida yo'q edi, shuning uchun publisher
`Could not find VTM resource for serial ...` -> "kamera OFLAYN" deb chiqardi va
panelda video umuman ko'rinmasdi (kamera esa onlayn va mobil ilovada ishlardi).

Sabab: `install_vtm_cache` har `make_client` da QAYTA o'rar va hisob
kalitini closure'dan olardi -> o'ramlar zanjirlanib, keyingi hisob oldingisining
keshini o'qib, uni O'Z keshiga yozardi.
"""
import pytest

from vizora.sources.ezviz import vtm_cache


@pytest.fixture
def cs(monkeypatch, tmp_path):
    """Toza `cloud_stream` moduli + vaqtinchalik kesh katalogi."""
    import pyezvizapi.cloud_stream as _cs
    from vizora.core.config import settings

    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ezviz_vtm_cache_seconds", 3600)

    cloud_calls = []

    def _orig_pagelist(client):
        cloud_calls.append(getattr(client, "_cs_account_key", None))
        return {"kim": getattr(client, "_cs_account_key", None)}

    monkeypatch.setattr(_cs, "get_vtm_page_list", _orig_pagelist, raising=False)
    monkeypatch.setattr(_cs, "get_vtdu_token_v2", lambda c, *a, **k: {"tok": 1},
                        raising=False)
    _cs._cloud_calls = cloud_calls
    return _cs


class _Client:
    def __init__(self, key):
        self._cs_account_key = key


def test_second_account_does_not_read_first_accounts_cache(cs):
    """A hisobi keshi to'la bo'lsa ham, B hisobi O'Z ro'yxatini olsin."""
    from vizora.sources.ezviz.cloud import install_vtm_cache

    vtm_cache.store("HISOB-A", "pagelist", {"kim": "HISOB-A"})

    install_vtm_cache()
    install_vtm_cache()          # takroriy chaqiruv zanjir yasamasin
    install_vtm_cache()

    got = cs.get_vtm_page_list(_Client("HISOB-B"))
    assert got == {"kim": "HISOB-B"}, "B hisobi A ning kameralarini oldi (oqish!)"
    assert vtm_cache.load("HISOB-B", "pagelist") == {"kim": "HISOB-B"}
    assert vtm_cache.load("HISOB-A", "pagelist") == {"kim": "HISOB-A"}   # buzilmadi


def test_own_cache_is_still_used(cs):
    """O'Z keshi bo'lsa bulutga borilmasin (tezlik shu keshda)."""
    from vizora.sources.ezviz.cloud import install_vtm_cache

    vtm_cache.store("HISOB-C", "pagelist", {"kim": "HISOB-C"})
    install_vtm_cache()
    cs._cloud_calls.clear()

    assert cs.get_vtm_page_list(_Client("HISOB-C")) == {"kim": "HISOB-C"}
    assert cs._cloud_calls == [], "kesh bor edi, baribir bulutga borildi"


def test_install_is_idempotent(cs):
    """Qayta o'rnatish o'ram USTIGA o'ram qo'ymasin (zanjir = oqish)."""
    from vizora.sources.ezviz.cloud import install_vtm_cache

    install_vtm_cache()
    first = cs.get_vtm_page_list
    for _ in range(5):
        install_vtm_cache()
    assert cs.get_vtm_page_list is first, "o'ram qayta-qayta qo'yildi (zanjirlanish)"


def test_hik_pager_is_client_bound_not_global(cs):
    """Hik sahifalovchisi MIJOZGA bog'lansin — keyingi EZVIZ hisobiga o'tmasin."""
    from vizora.sources.ezviz.cloud import install_vtm_cache

    install_vtm_cache()
    hik = _Client("HISOB-HIK")
    hik._cs_pagelist_impl = lambda c: {"kim": "hik-sahifalangan"}

    assert cs.get_vtm_page_list(hik) == {"kim": "hik-sahifalangan"}
    # EZVIZ hisobi Hik sahifalovchisini ISHLATMASIN
    assert cs.get_vtm_page_list(_Client("HISOB-EZ")) == {"kim": "HISOB-EZ"}


def test_no_key_means_no_cache_write(cs):
    """Kalitsiz mijoz (kutilmagan holat) hech kimning keshini buzmasin."""
    from vizora.sources.ezviz.cloud import install_vtm_cache

    install_vtm_cache()
    anon = type("X", (), {})()
    assert cs.get_vtm_page_list(anon) == {"kim": None}
    import glob, os
    from vizora.core.config import settings
    assert glob.glob(os.path.join(settings.ezviz_token_dir, "vtm-*")) == []
