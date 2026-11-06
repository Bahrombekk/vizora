"""EZVIZ hisoblari CRUD — EZVIZ bulut kameralari uchun login/parol qutilari.

Filial/tashkilot o'rniga: faqat zaruriy login/parol saqlanadi. Bir hisob bir
nechta kamerada ishlatilishi mumkin (parol takrorlanmaydi).
"""
import os
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.db import get_db
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.ezviz_account import EzvizAccount
from vizora.schemas.ezviz_account import (
    EzvizAccountCreate,
    EzvizAccountOut,
    EzvizAccountStats,
    EzvizAccountUpdate,
)
from vizora.core import crypto
from vizora.sources.ezviz import tokens as ezviz_tokens

router = APIRouter(prefix="/ezviz-accounts", tags=["ezviz-accounts"])

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
# Hisob yaratish/o'zgartirish/o'chirish — FAQAT admin: hisob bulut paroli bilan ishlaydi.
AdminOnly = Annotated[CurrentUser, Depends(require_role("admin"))]
Db = Annotated[AsyncSession, Depends(get_db)]


async def _get_or_404(db: AsyncSession, aid: uuid.UUID) -> EzvizAccount:
    obj = await db.get(EzvizAccount, aid)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "EZVIZ hisobi topilmadi")
    return obj


@router.get("", response_model=list[EzvizAccountStats])
async def list_accounts(db: Db, _: Admin) -> list[dict]:
    """Hisoblar + har birida kameralar (jami/onlayn/oqim yo'q) va sessiya holati."""
    accs = (await db.execute(select(EzvizAccount).order_by(EzvizAccount.label))).scalars().all()
    rows = (await db.execute(
        select(Camera.ezviz_account_id, func.count(),
               func.count().filter(Camera.status == "online"),
               func.count().filter(Camera.status == "no_stream"))
        .where(Camera.ezviz_account_id.is_not(None)).group_by(Camera.ezviz_account_id))).all()
    by = {aid: (n, on, ns) for aid, n, on, ns in rows}
    out = []
    for a in accs:
        n, on, ns = by.get(a.id, (0, 0, 0))
        try:
            session_at = os.path.getmtime(ezviz_tokens.token_path(str(a.id)))
        except OSError:
            session_at = None
        out.append({**EzvizAccountOut.model_validate(a).model_dump(),
                    "cameras": n, "online": on, "no_stream": ns, "session_at": session_at})
    return out


@router.post("", response_model=EzvizAccountOut, status_code=status.HTTP_201_CREATED)
async def create_account(payload: EzvizAccountCreate, db: Db, _: AdminOnly) -> EzvizAccount:
    obj = EzvizAccount(
        label=payload.label,
        account=payload.account,
        password_enc=crypto.encrypt(payload.password),
        provider=payload.provider,
        region=payload.region or None,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{aid}", response_model=EzvizAccountOut)
async def update_account(aid: uuid.UUID, payload: EzvizAccountUpdate, db: Db, _: AdminOnly) -> EzvizAccount:
    obj = await _get_or_404(db, aid)
    if payload.label is not None:
        obj.label = payload.label
    if payload.account is not None:
        obj.account = payload.account
    if payload.provider is not None:
        obj.provider = payload.provider
    if payload.region is not None:
        obj.region = payload.region or None
    if payload.password:
        obj.password_enc = crypto.encrypt(payload.password)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{aid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(aid: uuid.UUID, db: Db, _: AdminOnly) -> None:
    obj = await _get_or_404(db, aid)
    # Hisobga bog'liq kamera bo'lsa o'chirishni rad etamiz (oqim uzilmasin).
    used = (
        await db.execute(
            select(func.count()).select_from(Camera).where(Camera.ezviz_account_id == aid)
        )
    ).scalar_one()
    if used:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Bu hisob {used} ta kamerada ishlatilmoqda — avval ularni o'chiring yoki ko'chiring",
        )
    await db.delete(obj)
    await db.commit()
