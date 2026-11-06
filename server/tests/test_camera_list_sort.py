"""Kameralar ro'yxati: saralash kalitlari va "muammoli" filtri.

Kameralar sahifasidagi saralash tanlovi avval hech narsaga ulanmagan edi
(defaultValue, backend doim created_at bo'yicha qaytarardi)."""
from vizora.api.panel import cameras


def test_sort_keys_match_frontend_options():
    assert set(cameras._SORTS) == {"new", "name", "status", "seen"}


def test_unknown_sort_falls_back_to_new():
    assert cameras._SORTS.get("xyz", cameras._SORTS["new"]) is cameras._SORTS["new"]


def test_status_rank_puts_problems_first():
    sql = str(cameras._STATUS_RANK.compile(compile_kwargs={"literal_binds": True}))
    assert sql.index("no_stream") < sql.index("error") < sql.index("offline")
