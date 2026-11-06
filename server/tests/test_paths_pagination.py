"""MediaMTX `/v3/paths/list` sahifalanishi.

Nega: MediaMTX bu endpoint'ni SAHIFALAB qaytaradi (standarti 100 element).
Bir sahifani o'qish jimgina buziladi — 100-dan keyingi path chaqiruvchiga
"mavjud emas" ko'rinadi. Amalda: 159 path bo'lganda alifboda 2-sahifaga
tushgan `cam/f6f4...` DOIM TAYYOR isitgichga har sikl `ready=False, bytes=0`
ko'rinib, kamera har ~44s da qayta ishga tushirilib turdi va hech ochilmadi.
Xuddi shu javob idle-reaper (`_path_readers`) va probe'ni ham chalg'itadi:
tomoshabin bor kamera "reader'siz", oqayotgan kamera "offline" bo'lib qoladi.
"""
import httpx
import pytest

from vizora.media import mediamtx


def _paged_transport(total: int, per_page: int) -> httpx.MockTransport:
    """MediaMTX'ning sahifalash semantikasini takrorlaydi (page 0-indeksli)."""
    names = [f"cam/{i:04d}" for i in range(total)]
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 0))
        seen.append(page)
        chunk = names[page * per_page:(page + 1) * per_page]
        return httpx.Response(200, json={
            "itemCount": total,
            "pageCount": (total + per_page - 1) // per_page,
            "items": [{"name": n, "ready": True, "bytesReceived": 1, "readers": []}
                      for n in chunk],
        })

    t = httpx.MockTransport(handler)
    t.seen_pages = seen  # type: ignore[attr-defined]
    return t


@pytest.mark.asyncio
async def test_list_paths_barcha_sahifalarni_yigadi(monkeypatch):
    transport = _paged_transport(total=159, per_page=100)
    monkeypatch.setattr(mediamtx, "_client",
                        lambda: httpx.AsyncClient(base_url="http://mtx", transport=transport))

    items = await mediamtx.list_paths()

    assert len(items) == 159, "2-sahifa tushib qolgan — asl bug shu edi"
    assert {p["name"] for p in items} == {f"cam/{i:04d}" for i in range(159)}
    assert transport.seen_pages == [0, 1]


@pytest.mark.asyncio
async def test_list_paths_bitta_sahifa_bir_marta_soraladi(monkeypatch):
    transport = _paged_transport(total=7, per_page=100)
    monkeypatch.setattr(mediamtx, "_client",
                        lambda: httpx.AsyncClient(base_url="http://mtx", transport=transport))

    assert len(await mediamtx.list_paths()) == 7
    assert transport.seen_pages == [0]


@pytest.mark.asyncio
async def test_list_paths_xatoda_bosh_royxat(monkeypatch):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mediamtx yo'q", request=request)

    monkeypatch.setattr(mediamtx, "_client", lambda: httpx.AsyncClient(
        base_url="http://mtx", transport=httpx.MockTransport(boom)))

    assert await mediamtx.list_paths() == []


def test_hech_kim_paths_listni_ozi_chaqirmaydi():
    """Sahifalanishni chetlab o'tish qaytmasin — yolg'iz kirish nuqtasi list_paths()."""
    import pathlib
    root = pathlib.Path(mediamtx.__file__).parent.parent  # app/
    guilty = []
    for f in root.rglob("*.py"):
        if f.name == "mediamtx.py":
            continue
        if "paths/list" in f.read_text():
            guilty.append(str(f.relative_to(root)))
    assert not guilty, f"mediamtx.list_paths() dan foydalanish kerak: {guilty}"
