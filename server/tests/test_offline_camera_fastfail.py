"""OFLAYN kamera: keshni buzmasin va 18s kutmasin.

Real bazada 151 kameradan 31 tasi oflayn edi. Bulut oflayn qurilmani VTM
ro'yxatidan chiqarib tashlaydi, `pyezvizapi` esa "Could not find VTM resource"
xatosini ko'taradi. Ikki oqibat bor edi:
  1. `if not got_packets:` qo'rig'i buni ushlamasdi (oflayn kamera hech qachon
     paket bermaydi) -> VTM keshi o'chardi. Kesh HISOB bo'yicha, ya'ni bitta
     oflayn kamerani bosish o'sha hisobdagi HAMMA kameraning keshini yo'q qilib,
     ularning har birini yana ~4.5s metama'lumotga tushirardi.
  2. `wait_ready` publisher o'lganini tekshirmasdi -> foydalanuvchi 18s
     "yuklanmoqda" ko'rib, keyin hech narsa ko'rmasdi.
"""
import inspect
from types import SimpleNamespace

from vizora.sources.ezviz import manager, publish


# --- 1) Xatoni to'g'ri tanish ---

def test_recognises_pyezvizapi_missing_resource_message():
    """Kutubxonaning haqiqiy xabari tanilsin (cloud_stream.py dagi matn)."""
    exc = Exception("Could not find VTM resource for serial BD8712513 channel 1")
    assert publish._is_missing_resource(exc)
    assert publish._is_missing_resource(Exception("could not find vtm resource"))


def test_other_errors_are_not_treated_as_missing():
    """Boshqa xatolar (masalan eskirgan token) keshni O'CHIRISHI kerak."""
    for msg in ("Device offline or unreachable: timed out waiting for VTM stream data",
                "Timed out waiting for VTM stream info response",
                "Connection reset by peer"):
        assert not publish._is_missing_resource(Exception(msg)), msg
    assert not publish._is_missing_resource(None)


# --- 2) Cheksiz qayta urinish yo'q ---

def test_publisher_exits_after_repeated_missing_resource():
    """Oflayn qurilmani cheksiz urinish ma'nosiz — publisher chiqadi."""
    src = inspect.getsource(publish)
    assert "missing_tries" in src
    assert "if missing_tries >= 2:" in src, "chiqish sharti yo'q"


# --- 3) wait_ready o'lgan publisher'ni sezadi ---

def _pub(returncode):
    return SimpleNamespace(proc=SimpleNamespace(returncode=returncode),
                           stream_path="cam/x", last_request=0.0, provider="ezviz")


def test_publisher_dead_detects_exited_process(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {"c1": _pub(0)})
    assert manager._publisher_dead("cam/x") is True


def test_publisher_dead_false_while_running(monkeypatch):
    monkeypatch.setattr(manager, "_publishers", {"c1": _pub(None)})
    assert manager._publisher_dead("cam/x") is False


def test_publisher_dead_false_when_no_publisher(monkeypatch):
    """RTSP kamerada publisher YO'Q — uni "o'lgan" deb kutishni buzmaymiz."""
    monkeypatch.setattr(manager, "_publishers", {})
    assert manager._publisher_dead("cam/rtsp-1") is False
    monkeypatch.setattr(manager, "_publishers", {"c1": _pub(0)})
    assert manager._publisher_dead("cam/boshqa") is False


def test_wait_ready_checks_for_dead_publisher():
    """Tekshiruv kutish TSIKLI ichida bo'lishi kerak (bir marta emas)."""
    src = inspect.getsource(manager.wait_ready)
    assert "_publisher_dead(stream_path)" in src
    i_loop = src.index("while time.monotonic() < deadline")
    i_chk = src.index("_publisher_dead(stream_path)")
    assert i_chk > i_loop, "tekshiruv tsikldan tashqarida"
