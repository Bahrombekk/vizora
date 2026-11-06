"""ffmpeg ota (publisher) o'lganda birga o'lsin — NVENC sessiyasi oqmasин.

Muammo (sinovда topilган): publisher NOTOZA o'lса (SIGKILL/OOM) ffmpeg yetim
qolib NVENC sessiyaсини ushlаб turadi. Ko'p marta takrorlanса GPU sessiya limitи
tugab, "Could not open encoder" -> kameralar umuman ochilмаydi.
"""
import inspect

from vizora.sources.ezviz import publish


def test_ffmpeg_uses_pdeathsig():
    """Ikkala ffmpeg Popen ham preexec_fn=_die_with_parent bilan ochilsin."""
    src = inspect.getsource(publish)
    assert src.count("preexec_fn=_die_with_parent") == 2, \
        "ikkala ffmpeg Popen ham PR_SET_PDEATHSIG bilan bo'lishi kerak"


def test_die_with_parent_sets_pdeathsig():
    """_die_with_parent PR_SET_PDEATHSIG (1) ni SIGKILL bilan o'rnatadi."""
    src = inspect.getsource(publish._die_with_parent)
    assert "PR_SET_PDEATHSIG" in src or "prctl(1" in src
    assert "SIGKILL" in src


def test_die_with_parent_is_safe_off_linux():
    """Xato/Linux bo'lmaganда jimgina o'tishi kerak (Popen yiqilmasин)."""
    # chaqiruv hech qachon exception ko'tarmasligi kerak
    publish._die_with_parent()  # bu jarayonда prctl ishlaydi yoki jimgina o'tadi
