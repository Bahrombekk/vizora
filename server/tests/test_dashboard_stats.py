"""Dashboard tarixi: bo'laklash va bo'sh bo'laklar.

Grafik avval qo'lda chizilgan shakl edi; endi probe namunalaridan quriladi.
Ma'lumot yo'q bo'lak `None` bo'lishi shart — 0 bo'lsa yangi o'rnatilgan tizim
"hamma kamera o'chgan" bo'lib ko'rinadi.
"""
from vizora.services import stats


def test_bucketize_averages_and_leaves_gaps():
    start, span, n = 1000.0, 400.0, 4          # bo'lak = 100 s
    pts = [(1010, 10, 20), (1090, 20, 20), (1310, 7, 9)]
    out = stats.bucketize(pts, start, span, n)
    assert [p["online"] for p in out] == [15, None, None, 7]
    assert [p["total"] for p in out] == [20, None, None, 9]
    assert [p["t"] for p in out] == [1000, 1100, 1200, 1300]


def test_bucketize_ignores_points_outside_window():
    out = stats.bucketize([(999, 5, 5), (1400, 5, 5)], 1000.0, 400.0, 4)
    assert all(p["online"] is None for p in out)


def test_ranges_are_known():
    assert stats.RANGES["7d"][1] == 7 and stats.RANGES["30d"][1] == 30
    assert set(stats.RANGES) == {"24h", "7d", "30d"}


def test_day_window_starts_at_local_midnight():
    # 2026-10-02 06:30 UTC = 11:30 Toshkent (UTC+5)
    now = 1790922600.0
    start, span = stats.day_window(now, 7, 300)
    assert span == 7 * 86400
    # oyna oxiri = bugungi mahalliy yarim tun + 1 kun; boshlanishi mahalliy 00:00
    assert (start + 300 * 60) % 86400 == 0
    assert start <= now < start + span
    assert now - (start + 6 * 86400) < 86400


def test_sys_sample_reads_real_cpu_and_memory():
    """Monitoring CPU avval doim 0% edi (o'chiq warm-keeper'dan olinardi). Endi /proc dan:
    birinchi chaqiruv bazani oladi, ikkinchisi CPU foizini beradi."""
    stats._cpu_prev = None
    first = stats.sys_sample()
    assert "cpu" not in first
    sum(range(200000))                     # ozgina CPU ishi — jiffies o'zgarsin
    second = stats.sys_sample()
    assert 0.0 <= second.get("cpu", 0.0) <= 100.0
    assert second["mem_total_gb"] > 0 and 0 <= second["mem"] <= 100
