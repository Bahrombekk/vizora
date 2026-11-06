"""EZVIZ hisob (account) bo'yicha sessiya token'larini boshqarish.

Har EZVIZ HISOBI uchun bitta EzvizClient kesh'da saqlanadi (kalit = hisob id'si,
filial emas). Sessiya token'i diskka (settings.ezviz_token_dir/<account_id>.json)
yoziladi — publish.py subprocess shu fayldan o'qiydi. Token davriy yangilab
turiladi (parol/MFA qayta so'ralmasin).

Bloklovchi `requests` chaqiruvlari `asyncio.to_thread` orqali ishlatiladi.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from vizora.core.config import settings
from vizora.sources.ezviz.client import EzvizClient

log = logging.getLogger("vizora.ezviz.tokens")

# account_id (str) -> {"client", "account", "password", "region", "provider"}
_entries: dict[str, dict] = {}
_locks: dict[str, asyncio.Lock] = {}


def _lock(ent_id: str) -> asyncio.Lock:
    lk = _locks.get(ent_id)
    if lk is None:
        lk = _locks[ent_id] = asyncio.Lock()
    return lk


def token_path(ent_id: str) -> str:
    os.makedirs(settings.ezviz_token_dir, exist_ok=True)
    return os.path.join(settings.ezviz_token_dir, f"{ent_id}.json")


def _write_token(ent_id: str, client: EzvizClient) -> str:
    path = token_path(ent_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(client.token_dict(), f)
    os.replace(tmp, path)  # atomik almashtirish (publisher yarim faylni o'qimasin)
    return path


async def _login(ent_id: str, account: str, password: str, region: str,
                 provider: str = "ezviz") -> EzvizClient:
    client = EzvizClient(account, password, region, provider=provider)
    await asyncio.to_thread(client.login)
    _entries[ent_id] = {
        "client": client, "account": account, "password": password,
        "region": region, "provider": provider,
    }
    _write_token(ent_id, client)
    log.info("EZVIZ login muvaffaqiyatli (hisob %s, provayder=%s)", ent_id, provider)
    return client


async def ensure_client(ent_id: str, account: str, password: str, region: str,
                        provider: str = "ezviz") -> EzvizClient:
    """Hisob uchun loginlangan client'ni qaytaradi (kerak bo'lsa login qiladi).

    Login ma'lumoti (yoki provayder) o'zgargan bo'lsa qaytadan login qilinadi.
    """
    async with _lock(ent_id):
        entry = _entries.get(ent_id)
        if (
            entry is None
            or entry["account"] != account
            or entry["password"] != password
            or entry["region"] != region
            or entry.get("provider") != provider
        ):
            return await _login(ent_id, account, password, region, provider)
        return entry["client"]


async def ensure_token_file(ent_id: str, account: str, password: str, region: str,
                            provider: str = "ezviz") -> str:
    """Loginni kafolatlab, token faylini yozadi va yo'lini qaytaradi."""
    await ensure_client(ent_id, account, password, region, provider)
    return token_path(ent_id)


async def get_devices(ent_id: str, account: str, password: str, region: str,
                      provider: str = "ezviz") -> dict:
    """EZVIZ/Hik-Connect qurilmalar ro'yxati: {serial: {name, status, ...}}."""
    client = await ensure_client(ent_id, account, password, region, provider)
    try:
        return await asyncio.to_thread(client.get_devices)
    except Exception:  # noqa: BLE001 — sessiya eskirgan bo'lishi mumkin, bir marta qayta login
        client = await _login(ent_id, account, password, region, provider)
        return await asyncio.to_thread(client.get_devices)


async def get_channels(ent_id: str, account: str, password: str, region: str,
                       provider: str = "ezviz") -> list[dict]:
    """Ulangan NVR/qurilma kanallari: [{serial, channel, name}]."""
    client = await ensure_client(ent_id, account, password, region, provider)
    try:
        return await asyncio.to_thread(client.get_channels)
    except Exception:  # noqa: BLE001 — sessiya eskirgan bo'lishi mumkin, bir marta qayta login
        client = await _login(ent_id, account, password, region, provider)
        return await asyncio.to_thread(client.get_channels)


async def refresh_all() -> None:
    """Barcha kesh'dagi sessiyalarni yangilab, token fayllarini qayta yozadi.

    Har hisob uchun PER-ACCOUNT lock ostida — refresh client'ni mutatsiya qiladi,
    bir vaqtda ensure_client/get_devices uni o'qib/almashtirib turishi mumkin."""
    for acc_id in list(_entries):
        async with _lock(acc_id):
            entry = _entries.get(acc_id)  # lock ichida qayta o'qiymiz (o'zgargan bo'lishi mumkin)
            if entry is None:
                continue
            client: EzvizClient = entry["client"]
            try:
                await asyncio.to_thread(client.refresh_session)
                _write_token(acc_id, client)
                log.info("EZVIZ token yangilandi (hisob %s)", acc_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("EZVIZ token yangilash xatosi (hisob %s): %s", acc_id, exc)
