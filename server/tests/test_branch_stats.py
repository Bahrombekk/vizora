"""Filiallar ro'yxati: har filial uchun hudud/kamera/onlayn sonlari.

Avval sahifa soxta "+1/+12" va har doim "Faol" ko'rsatardi — filialda holat maydoni
yo'q, kamera soni esa umuman ko'rinmasdi."""
import uuid
from datetime import datetime, timezone

from vizora.schemas.branch import BranchOut, BranchStats


def test_stats_extends_out_with_zero_defaults():
    base = {"id": uuid.uuid4(), "name": "A", "code": "A1", "created_at": datetime.now(timezone.utc)}
    s = BranchStats(**base)
    assert (s.zones, s.cameras, s.online) == (0, 0, 0)
    assert set(BranchOut.model_fields) <= set(BranchStats.model_fields)


def test_unassigned_route_is_registered_before_id_routes():
    from vizora.api.panel import branches
    paths = [r.path for r in branches.router.routes]
    assert "/branches/unassigned" in paths


def test_zone_stats_defaults():
    from vizora.schemas.zone import ZoneOut, ZoneStats
    base = {"id": uuid.uuid4(), "branch_id": uuid.uuid4(), "name": "Z", "code": "Z1",
            "location": None, "created_at": datetime.now(timezone.utc)}
    s = ZoneStats(**base)
    assert (s.cameras, s.online) == (0, 0)
    assert set(ZoneOut.model_fields) <= set(ZoneStats.model_fields)


def test_ezviz_account_stats_defaults_and_no_password():
    from vizora.schemas.ezviz_account import EzvizAccountStats
    s = EzvizAccountStats(id=uuid.uuid4(), label="L", account="a@b", provider="ezviz",
                          region=None, created_at=datetime.now(timezone.utc))
    assert (s.cameras, s.online, s.no_stream, s.session_at) == (0, 0, 0, None)
    assert "password" not in EzvizAccountStats.model_fields and "password_enc" not in EzvizAccountStats.model_fields


def test_camera_list_accepts_account_filter():
    import inspect
    from vizora.api.panel import cameras
    assert "ezviz_account_id" in inspect.signature(cameras.list_cameras).parameters
    assert "ezviz_account_id" in inspect.signature(cameras.camera_counts).parameters


def test_client_stats_counts_follow_can_access():
    """Kartadagi "N kamera ko'ra oladi" — API bergan ruxsat bilan bir xil qoida."""
    from types import SimpleNamespace
    from vizora.schemas.auth import ApiClientStats
    from vizora.services.access import can_access
    assert ApiClientStats.model_fields["cameras_allowed"].default == 0
    fid, cid = uuid.uuid4(), uuid.uuid4()
    cam = SimpleNamespace(id=cid, zone_id=fid)
    fac = SimpleNamespace(id=fid, branch_id=uuid.uuid4())
    assert can_access(SimpleNamespace(scopes={"zones": [str(fid)]}), cam, fac)
    assert not can_access(SimpleNamespace(scopes={"zones": []}), cam, fac)
