"""Fon snapshot-warmer: barcha EZVIZ kameralarni oldindan to'ldiradi.

Nega: avval snapshot faqat TALAB bo'yicha (grid kartani ko'rsatganda) olinardi —
ko'rinmayotgan kamera hech qachon snapshot olmasdi va grid ochilganda qora
kvadrat ko'rinardi. Warmer ularni sekin aylanib to'ldiradi.
"""
import inspect

from vizora.core.config import settings
from vizora.sources.ezviz import manager


def test_warmer_is_registered_in_lifespan():
    """lifespan.py da ishga tushirilishi kerak (aks holda hech qachon ishlamaydi)."""
    import vizora.lifespan as m
    src = inspect.getsource(m)
    assert "run_snapshot_warmer" in src
    assert "run_snapshot_warmer()" in src


def test_warmer_respects_disable_flag():
    """Sozlama o'chirilgan bo'lsa warmer darhol chiqishi kerak."""
    src = inspect.getsource(manager.run_snapshot_warmer)
    assert "ezviz_snapshot_warm_enabled" in src
    assert "ezviz_snapshot_enabled" in src


def test_warmer_paces_between_cameras():
    """Kameralar orasida PAUZA (bulutni hammalab yubormaslik) — gap sozlamasi."""
    src = inspect.getsource(manager.run_snapshot_warmer)
    assert "ezviz_snapshot_warm_gap_seconds" in src
    assert "asyncio.sleep(gap)" in src


def test_warmer_error_isolation():
    """Bitta kamera xatosi butun siklni to'xtatmasligi kerak."""
    src = inspect.getsource(manager.run_snapshot_warmer)
    # kamera ichki try/except bor
    assert src.count("except Exception") >= 2


def test_defaults_present():
    assert settings.ezviz_snapshot_warm_enabled in (True, False)
    assert settings.ezviz_snapshot_warm_gap_seconds > 0


# --- Sessiya thrashing'ini oldini olish ---

def test_device_error_does_not_pop_client():
    """Qurilma-darajali xato (kadr olib bo'lmadi) HISOB client'ini o'chirmasligi kerak.

    Avval har xatoда `_clients.pop` qilinardi -> bitta doim-xato kamera butun
    hisob sessiyasini qayta-qayta o'chirib, boshqa kameralar snapshot ololmasди."""
    from vizora.services import snapshot
    src = inspect.getsource(snapshot.refresh)
    # `_clients.pop` FAQAT sessiya xatosi (not device_level) ostida bo'lishi kerak
    assert "device_level" in src
    assert "if not device_level:" in src
    i_guard = src.index("if not device_level:")
    i_pop = src.index("_clients.pop", i_guard)
    # pop guard'dan KEYIN (uning ichida) bo'lishi kerak — orada boshqa pop bo'lmasin
    # HAQIQIY chaqiruv (izoh emas) faqat bitta va guard ichida bo'lsin
    calls = [ln for ln in src.splitlines()
             if "_clients.pop(" in ln and not ln.strip().startswith("#")]
    assert len(calls) == 1, f"kutilmagan pop chaqiruvi: {calls}"
    assert i_pop > i_guard
