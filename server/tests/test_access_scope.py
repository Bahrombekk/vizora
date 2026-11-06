"""can_access — moslashuvchan scope + buzuq/None scope'larga chidamlilik."""
import uuid
from types import SimpleNamespace

from vizora.services.access import can_access


def _cam(zone_id=None):
    return SimpleNamespace(id=uuid.uuid4(), zone_id=zone_id)


def _client(scopes):
    return SimpleNamespace(scopes=scopes)


def test_all_true_grants():
    assert can_access(_client({"all": True}), _cam(), None)


def test_all_string_false_does_not_grant():
    # Audit topilmasi: "false" (string) truthy bo'lib BUTUN kirish bermasin.
    assert not can_access(_client({"all": "false"}), _cam(), None)


def test_cameras_string_no_substring_match():
    cam = _cam()
    # cameras list emas, balki string bo'lsa — substring moslik bo'lmasin.
    assert not can_access(_client({"cameras": str(cam.id)}), cam, None)
    assert can_access(_client({"cameras": [str(cam.id)]}), cam, None)


def test_zone_scope():
    fid = uuid.uuid4()
    cam = _cam(zone_id=fid)
    assert can_access(_client({"zones": [str(fid)]}), cam, None)


def test_branch_scope():
    eid = uuid.uuid4()
    cam = _cam(zone_id=uuid.uuid4())
    fac = SimpleNamespace(branch_id=eid)
    assert can_access(_client({"branches": [str(eid)]}), cam, fac)


def test_none_and_malformed_scopes_deny():
    assert not can_access(_client(None), _cam(), None)
    assert not can_access(_client("nonsense"), _cam(), None)
    assert not can_access(_client({}), _cam(), None)


def test_unassigned_camera_only_via_all_or_cameras():
    cam = _cam(zone_id=None)
    assert not can_access(_client({"zones": [str(uuid.uuid4())]}), cam, None)
    assert can_access(_client({"cameras": [str(cam.id)]}), cam, None)
