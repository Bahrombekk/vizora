"""MediaMTX `/v3/paths/list` SAHIFALANADI — bu ko'p kamerada hammasini buzardi.

Standart holda endpoint 100 element/sahifa qaytaradi. Kod faqat BIRINCHI sahifani
o'qigani uchun 158 kamerada (asosiy + `/sub` = 221 path) kameralarning yarmidan
ko'pi "yo'q" bo'lib ko'rinardi:
  * `_path_ready` ularni HECH QACHON tayyor deb ko'rmasdi -> har ochilish
    `wait_ready` ning 18s chegarasiga urilib "kamera ochilmadi" bo'lardi;
  * `probe` ham holatni xato aniqlardi (oqib turgan kamera "oflayn").
O'lchangan: ffmpeg MediaMTX'ga 102 marta publish qilgan, ilova esa faqat 24 marta
"path ready" ko'rgan. Tuzatilgandan keyin o'sha kameralar 18.1s -> 1.5-2.6s.

34 kamerada (<100 path) xato KO'RINMAYDI — shuning uchun test bilan qulflanadi.
"""
import inspect
import pathlib

import pytest

from vizora.media import mediamtx


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code != 200:
            raise AssertionError(f"HTTP {self.status_code}")


class _FakeClient:
    """`paths/list` ni sahifalab qaytaradigan minimal soxta klient."""

    def __init__(self, pages, single=None):
        self.pages = pages
        self.single = single
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        self.calls.append((url, params))
        if url.startswith("/v3/paths/get/"):
            return _FakeResponse(self.single, 200 if self.single else 404)
        page = int((params or {}).get("page", 0))
        return _FakeResponse({"itemCount": sum(len(p) for p in self.pages),
                              "pageCount": len(self.pages),
                              "items": self.pages[page] if page < len(self.pages) else []})


@pytest.mark.asyncio
async def test_list_paths_reads_every_page(monkeypatch):
    """Uch sahifadagi HAMMA element qaytishi kerak, faqat birinchisi emas."""
    pages = [[{"name": f"cam/{i}"} for i in range(100)],
             [{"name": f"cam/{i}"} for i in range(100, 200)],
             [{"name": f"cam/{i}"} for i in range(200, 221)]]
    fake = _FakeClient(pages)
    monkeypatch.setattr(mediamtx, "_client", lambda: fake)
    got = await mediamtx.list_paths()
    assert len(got) == 221, "sahifalar to'liq o'qilmadi"
    assert got[-1]["name"] == "cam/220"
    assert len([c for c in fake.calls if c[0] == "/v3/paths/list"]) == 3


@pytest.mark.asyncio
async def test_list_paths_single_page_stops_immediately(monkeypatch):
    """Bitta sahifa bo'lsa qo'shimcha so'rov yuborilmaydi."""
    fake = _FakeClient([[{"name": "cam/1"}]])
    monkeypatch.setattr(mediamtx, "_client", lambda: fake)
    assert len(await mediamtx.list_paths()) == 1
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_get_path_uses_single_lookup(monkeypatch):
    """Bitta path uchun butun ro'yxat tortilmasin — `paths/get` ishlatiladi."""
    fake = _FakeClient([[]], single={"name": "cam/x", "ready": True})
    monkeypatch.setattr(mediamtx, "_client", lambda: fake)
    p = await mediamtx.get_path("cam/x")
    assert p and p["ready"] is True
    assert fake.calls == [("/v3/paths/get/cam/x", None)]


@pytest.mark.asyncio
async def test_get_path_returns_none_when_absent(monkeypatch):
    fake = _FakeClient([[]], single=None)
    monkeypatch.setattr(mediamtx, "_client", lambda: fake)
    assert await mediamtx.get_path("cam/yo-q") is None


def test_path_ready_does_not_scan_the_whole_list():
    """`_path_ready` bitta path so'rashi kerak (sahifalash tuzog'iga qaytmasin)."""
    from vizora.sources.ezviz import manager
    src = inspect.getsource(manager._path_ready)
    # Izohlarda "paths/list" so'zi bo'lishi mumkin — CHAQIRUVni tekshiramiz
    # (endpoint qo'shtirnoq ichida yozilgan bo'lsa, demak so'rov yuborilyapti).
    assert '"/v3/paths/list"' not in src, "yana butun ro'yxat tortilyapti"
    assert "mediamtx.get_path(" in src


def test_only_mediamtx_helper_calls_paths_list_directly():
    """`/v3/paths/list` FAQAT [[mediamtx.list_paths]] ichida chaqirilsin.

    Boshqa joyda to'g'ridan chaqirilsa sahifalash yana e'tibordan chetda qoladi."""
    root = pathlib.Path(mediamtx.__file__).parent.parent      # app/
    offenders = []
    for f in root.rglob("*.py"):
        txt = f.read_text(encoding="utf-8")
        if '"/v3/paths/list"' in txt and f.name != "mediamtx.py":
            offenders.append(str(f.relative_to(root)))
    assert not offenders, f"sahifalanmagan chaqiruv: {offenders}"
