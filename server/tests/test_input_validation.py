"""Kirish tekshiruvlari (audit 2026-11-03, Low bandlar)."""
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from vizora.api.media.ws import parse_path
from vizora.api.patch import reject_null_columns
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.schemas.camera import CameraCreate, CameraUpdate, check_rtsp_url
from vizora.sources import isup
from vizora.sources.ezviz.client import EzvizClient, EzvizLoginError

_U = "11111111-1111-1111-1111-111111111111"


def test_trailing_newline_rejected_everywhere():
    """Python `$` oxirgi "\\n" dan oldin ham mos keladi — `\\Z` bilan yopildi."""
    with pytest.raises(ValueError):
        check_rtsp_url("rtsp://cam.local/a\n")
    assert parse_path(f"cam/{_U}\n") is None
    assert parse_path(f"cam/{_U}") is not None
    assert not isup.DEVICE_ID_RE.match("DEV1\n")
    assert not isup.KEY_RE.match("abcdefgh1\n")
    from vizora.api.media.whep import _PATH_RE
    assert _PATH_RE.match(f"cam/{_U}/whep\n") is None


def test_ezviz_serial_charset():
    ok = dict(name="x", source_type="ezviz", ezviz_account_id=uuid.uuid4())
    CameraCreate(**ok, ezviz_serial="BD8712447")
    for bad in ("BD87 12447", "BD;rm -rf", "../x", "a\nb"):
        with pytest.raises(ValidationError):
            CameraCreate(**ok, ezviz_serial=bad)
    with pytest.raises(ValidationError):
        CameraUpdate(ezviz_serial="x y")


def test_verify_code_charset():
    CameraUpdate(ezviz_verify_code="SCXYKW")
    CameraUpdate(ezviz_verify_code="")          # "" — kodni o'chirish
    with pytest.raises(ValidationError):
        CameraUpdate(ezviz_verify_code="--evil")


@pytest.mark.parametrize("domain", ["evil.com", "apiisgp.ezvizlife.com.evil.com",
                                    "127.0.0.1", "api.hik-connect.com/x", ""])
def test_cloud_domain_rechecked_at_use(domain):
    """Bazadagi region yoki bulut redirect'i begona domen bo'lsa login qilinmaydi."""
    with pytest.raises(EzvizLoginError):
        EzvizClient("a@b.c", "pw", domain)


def test_cloud_domain_ok():
    assert EzvizClient("a@b.c", "pw", "API.Hik-Connect.com").api_url == "api.hik-connect.com"


def test_patch_null_on_not_null_column_is_422():
    with pytest.raises(HTTPException) as e:
        reject_null_columns(Camera(), {"name": None})
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        reject_null_columns(Branch(), {"name": None})
    reject_null_columns(Camera(), {"zone_id": None})        # nullable — ruxsat
    reject_null_columns(Camera(), {"rtsp_url": None})          # model ustuni emas
