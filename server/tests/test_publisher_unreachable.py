"""Bulutda bor, lekin oqim bermaydigan qurilmada publisher chiqishi.

161 kameralik sweep'da (2026-09-10) 14 kamera shu holatda edi: bulut qurilmani
taniydi, `stream.start()` o'tadi, video kelmaydi. Cheksiz qayta urinish o'rniga
publisher 2 urinishdan keyin chiqadi — [[ws_stream]] hub'i o'limni sezib aniq
xabar beradi (aks holda 90s "kutilmoqda" ko'rsatilardi).
"""
import re
from pathlib import Path

import pytest

from vizora.sources.ezviz.publish import _is_missing_resource, _is_unreachable

SRC = Path(__file__).resolve().parents[1] / "vizora" / "sources" / "ezviz" / "publish.py"


def test_unreachable_matches_real_pyezvizapi_message():
    exc = RuntimeError("Device offline or unreachable: timed out waiting for VTM stream data")
    assert _is_unreachable(exc) is True
    assert _is_missing_resource(exc) is False


def test_unreachable_matches_either_marker_case_insensitively():
    assert _is_unreachable(Exception("DEVICE OFFLINE OR UNREACHABLE")) is True
    assert _is_unreachable(Exception("Timed out waiting for VTM stream data")) is True


def test_unreachable_ignores_unrelated_errors():
    for msg in ("Could not find VTM resource for serial ABC",
                "write queue is full", "Connection reset by peer", ""):
        assert _is_unreachable(Exception(msg)) is False
    assert _is_unreachable(None) is False


def test_missing_resource_still_detected():
    assert _is_missing_resource(Exception("Could not find VTM resource for serial X")) is True
    assert _is_missing_resource(None) is False


def test_vtm_cache_not_invalidated_when_device_merely_silent():
    """Kesh HISOB bo'yicha: "qurilma oqim bermadi" da uni o'chirish o'sha hisobdagi
    HAMMA kamerani sovutadi (o'lchandi: keyingi har ochilish +2.4s). Har ikki siklda
    ham `invalidate` sharti `_is_unreachable` ni HAM tekshirishi shart."""
    src = SRC.read_text(encoding="utf-8")
    guards = re.findall(
        r"if not got_packets and not _is_missing_resource\(last_exc\) "
        r"and not _is_unreachable\(last_exc\):\s*\n\s*vtm_cache\.invalidate\(", src)
    assert len(guards) == 2, f"kutilgan 2 ta himoyalangan invalidate, topildi {len(guards)}"
    # Himoyasiz invalidate qolmasin
    assert src.count("vtm_cache.invalidate(") == 2


def test_both_publisher_loops_exit_on_unreachable():
    """Ikkala siklda ham (oddiy va drop_corrupt) chiqish sharti bo'lishi kerak —
    biri unutilsa oflayn kamera yana 90s hang qiladi."""
    src = SRC.read_text(encoding="utf-8")
    # `_is_unreachable(last_exc) and not got_packets` -> unreach_tries -> return
    blocks = re.findall(
        r"if _is_unreachable\(last_exc\) and not got_packets:\s*\n"
        r"\s*unreach_tries \+= 1\s*\n"
        r"\s*if unreach_tries >= 2:", src)
    assert len(blocks) == 2, f"kutilgan 2 ta chiqish sharti, topildi {len(blocks)}"
    # Har bir siklda hisoblagich e'lon qilingan (NameError bo'lmasin)
    assert src.count("unreach_tries = 0      # ketma-ket") == 2
    # "paket keldi" bo'lsa chiqmaydi (sog'lom oqimdagi blip publisher'ni o'ldirmasin)
    assert "not got_packets" in src
