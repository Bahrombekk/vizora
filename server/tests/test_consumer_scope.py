"""Iste'molchi scope tekshiruvi va kodek normalizatsiyasi."""
from types import SimpleNamespace

from vizora.services.access import can_access as _can_access
from vizora.services.probe import _norm_codec


def _objs(scopes, cam_id="c1", fac_id="f1", ent_id="e1"):
    client = SimpleNamespace(scopes=scopes)
    camera = SimpleNamespace(id=cam_id, zone_id=fac_id)
    zone = SimpleNamespace(branch_id=ent_id)
    return client, camera, zone


def test_scope_all():
    c, cam, fac = _objs({"all": True})
    assert _can_access(c, cam, fac)


def test_scope_by_camera():
    c, cam, fac = _objs({"cameras": ["c1"]})
    assert _can_access(c, cam, fac)
    c2, cam2, fac2 = _objs({"cameras": ["other"]})
    assert not _can_access(c2, cam2, fac2)


def test_scope_unassigned_camera():
    # Hududga biriktirilmagan kamera: faqat all/cameras orqali ruxsat
    c, cam, fac = _objs({"cameras": ["c1"]}, fac_id=None)
    assert _can_access(c, cam, None)
    c2, cam2, _ = _objs({"zones": ["f1"]}, fac_id=None)
    assert not _can_access(c2, cam2, None)


def test_scope_by_zone():
    c, cam, fac = _objs({"zones": ["f1"]})
    assert _can_access(c, cam, fac)
    c2, cam2, fac2 = _objs({"zones": ["other"]})
    assert not _can_access(c2, cam2, fac2)


def test_scope_by_branch():
    c, cam, fac = _objs({"branches": ["e1"]})
    assert _can_access(c, cam, fac)


def test_scope_empty_denies():
    c, cam, fac = _objs({})
    assert not _can_access(c, cam, fac)


def test_norm_codec():
    assert _norm_codec("hevc") == "h265"
    assert _norm_codec("h265") == "h265"
    assert _norm_codec("h264") == "h264"
    assert _norm_codec("avc") == "h264"
    assert _norm_codec(None) is None
