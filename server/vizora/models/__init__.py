"""Barcha ORM modellarni bitta joydan import qilish (Alembic metadata uchun)."""
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.models.ezviz_account import EzvizAccount
from vizora.models.camera import Camera
from vizora.models.client import ApiClient, User
from vizora.models.isup import IsupDevice, IsupKey

__all__ = ["Branch", "Zone", "EzvizAccount", "Camera", "User", "ApiClient", "IsupKey", "IsupDevice"]
