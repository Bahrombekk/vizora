"""Bulut xatosi VTM keshini YO'Q QILMASLIGI kerak — "kameralar sekin ochilyabdi".

O'lchangan (2026-09-05, jonli): `warm()` ilgari keshni OLDIN o'chirib, keyin
bulutdan olardi. 12:50:50 da bulut 504 Gateway Timeout berdi -> kesh bo'sh qoldi
va keyingi isitishgacha (TTL/2 = 30 daq) o'sha hisobdagi kameralar to'liq
metama'lumot so'roviga tushdi: `bulut ulandi (VTM)` 0.14s o'rniga 6.08s,
ochilish 3.0s o'rniga 8.0s.
"""
import inspect

import pytest

from vizora.sources.ezviz import manager, vtm_cache


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    from vizora.core.config import settings
    monkeypatch.setattr(settings, "ezviz_token_dir", str(tmp_path))
    monkeypatch.setattr(settings, "ezviz_vtm_cache_seconds", 3600)
    return tmp_path


def test_warm_failure_keeps_old_cache(cache_dir, monkeypatch):
    """Bulut yiqilsa ESKI kesh joyida qolsin (bo'shab qolmasin)."""
    vtm_cache.store("acc-1", "pagelist", {"eski": True})

    import pyezvizapi.cloud_stream as _cs

    def _boom(_client):
        raise RuntimeError("504 Server Error: Gateway Timeout")

    monkeypatch.setattr(_cs, "get_vtm_page_list", _boom, raising=False)
    monkeypatch.setattr("vizora.sources.ezviz.cloud.make_client",
                        lambda *a, **kw: object())

    assert vtm_cache.warm(str(cache_dir / "acc-1.json")) is False
    # ENG MUHIMI: kesh hamon o'qilsin — aks holda har ochilish ~6s ortiq
    assert vtm_cache.load("acc-1", "pagelist") == {"eski": True}


def test_warm_bypasses_cache_read_so_it_actually_refetches(cache_dir, monkeypatch):
    """Isitish keshdan O'QIMASIN — aks holda u hech narsa yangilamaydi."""
    vtm_cache.store("acc-2", "pagelist", {"eski": True})
    calls = []

    import pyezvizapi.cloud_stream as _cs

    def _fetch(_client):
        calls.append(1)
        # publish._install_vtm_cache o'rami kabi: keshdan o'qib, bo'lmasa yozadi
        hit = vtm_cache.load("acc-2", "pagelist")
        if hit is not None:
            return hit
        vtm_cache.store("acc-2", "pagelist", {"yangi": True})
        return {"yangi": True}

    monkeypatch.setattr(_cs, "get_vtm_page_list", _fetch, raising=False)
    monkeypatch.setattr("vizora.sources.ezviz.cloud.make_client",
                        lambda *a, **kw: object())

    assert vtm_cache.warm(str(cache_dir / "acc-2.json")) is True
    assert calls == [1]
    assert vtm_cache.load("acc-2", "pagelist") == {"yangi": True}


def test_bypass_is_thread_local(cache_dir):
    """`bypass_read()` faqat O'Z oqumida ta'sir qilsin — probe'ning
    `available_channels()` kabi o'quvchilari ko'r bo'lib qolmasin."""
    import threading

    vtm_cache.store("acc-3", "pagelist", {"bor": True})
    seen = {}

    def _other():
        seen["val"] = vtm_cache.load("acc-3", "pagelist")

    with vtm_cache.bypass_read():
        assert vtm_cache.load("acc-3", "pagelist") is None      # shu oqumda ko'r
        t = threading.Thread(target=_other)
        t.start()
        t.join()
    assert seen["val"] == {"bor": True}                          # boshqa oqum ko'radi


def test_warm_loop_does_not_invalidate_first():
    """`_warm_vtm_cache` keshni OLDINDAN o'chirmasin (regressiya qulfi)."""
    # izohlarni tashlaymiz — "invalidate" so'zi tushuntirish matnida uchraydi
    code = "\n".join(ln.split("#")[0] for ln in
                     inspect.getsource(manager._warm_vtm_cache).splitlines())
    assert "invalidate(" not in code, \
        "keshni oldindan o'chirish = bulut xatosida bo'sh kesh (sekin ochilish)"


def test_failed_warm_retries_sooner():
    """Isitish yiqilsa keyingi urinish TTL/2 emas, TEZROQ bo'lsin."""
    src = inspect.getsource(manager.run_supervisor)
    assert "vtm_gap" in src, "yiqilganda qisqa oraliqqa o'tish yo'q"
    assert "_warm_vtm_cache()" in src
