"""Nosozlik kategoriyasi: "online, lekin oqim yo'q" kameralarni ko'rinadigan qilish.

Muammo: bulut qurilmani online deydi, `stream.start()` o'tadi, video kelmaydi.
Panelda bunday kamera "Onlayn" ko'rinardi — foydalanuvchi bosib 40s kutardi va
sabab hech qayerda yozilmagan edi. Endi publisher ANIQ chiqish kodi bilan
tugaydi ([[publish.EXIT_NO_STREAM]]), `manager` shuni eslab qoladi va `probe`
kamerani `status="no_stream"` deb yozadi — Monitoring sahifasida alohida
kategoriya sifatida sababi bilan ko'rinadi.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from vizora.services import probe
from vizora.sources.ezviz import manager

PUB_SRC = Path(__file__).resolve().parents[1] / "vizora" / "sources" / "ezviz" / "publish.py"


def _clear():
    manager._faults.clear()


def test_exit_codes_are_unique_and_mapped():
    from vizora.sources.ezviz import publish

    assert publish.EXIT_NO_STREAM != publish.EXIT_MISSING
    assert manager._EXIT_FAULTS[publish.EXIT_NO_STREAM] == manager.FAULT_NO_STREAM
    assert manager._EXIT_FAULTS[publish.EXIT_MISSING] == manager.FAULT_MISSING


def test_publisher_exits_with_the_fault_codes():
    """Kodlar publish.py'da HAQIQATAN ishlatiladi (aks holda kategoriya bo'sh
    qoladi va nosozlik yana yashirin bo'lib ketadi)."""
    src = PUB_SRC.read_text(encoding="utf-8")
    assert re.search(r"raise SystemExit\(EXIT_MISSING\)", src)
    assert re.search(r"raise SystemExit\(EXIT_NO_STREAM\)", src)


def test_note_exit_records_and_counts_tries():
    _clear()
    manager.note_publisher_exit("cam-1", 3)
    f = manager.faults()["cam-1"]
    assert f["reason"] == manager.FAULT_NO_STREAM
    assert f["tries"] == 1
    manager.note_publisher_exit("cam-1", 3)
    assert manager.faults()["cam-1"]["tries"] == 2
    _clear()


def test_note_exit_ignores_normal_and_signal_exits():
    _clear()
    for code in (0, 1, None, -9, 2):
        manager.note_publisher_exit("cam-x", code)
    assert manager.faults() == {}


def test_clear_fault_on_frames():
    _clear()
    manager.note_publisher_exit("cam-2", 4)
    assert manager.faults()["cam-2"]["reason"] == manager.FAULT_MISSING
    manager.clear_fault("cam-2")
    assert "cam-2" not in manager.faults()
    manager.clear_fault("cam-2")  # ikkinchi marta xato bermaydi
    _clear()


def _snap(status="online"):
    return SimpleNamespace(id="cam-9", status=status, last_seen_at=None)


def test_plan_marks_fault_as_separate_category_not_online():
    plan = probe._Plan()
    plan.mark(_snap(), True, None, fault="no_stream")
    assert plan.nostream_ids == ["cam-9"]
    assert plan.online_ids == []
    assert plan.changes == [("cam-9", "no_stream")]


def test_plan_does_not_repush_same_status():
    plan = probe._Plan()
    plan.mark(_snap(status="no_stream"), True, None, fault="no_stream")
    assert plan.nostream_ids == ["cam-9"]   # DB'da yozib turamiz
    assert plan.changes == []                # lekin Redis'ga push qilmaymiz


def test_plan_healthy_camera_returns_to_online():
    plan = probe._Plan()
    plan.mark(_snap(status="no_stream"), True, None)
    assert plan.online_ids == ["cam-9"]
    assert plan.changes == [("cam-9", "online")]


def test_status_column_fits_the_new_value():
    """`Camera.status` — String(10); "no_stream" 9 belgi, sig'adi."""
    from vizora.models.camera import Camera

    assert len("no_stream") <= Camera.__table__.c.status.type.length
