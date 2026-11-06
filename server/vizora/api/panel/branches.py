"""Filiallar CRUD — ixtiyoriy tashkiliy birlik (kameralarni guruhlash uchun)."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.db import get_db
from vizora.api.patch import apply_patch
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.schemas.branch import BranchCreate, BranchOut, BranchStats, BranchUpdate

router = APIRouter(prefix="/branches", tags=["branches"])

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
Db = Annotated[AsyncSession, Depends(get_db)]


async def _get_or_404(db: AsyncSession, eid: uuid.UUID) -> Branch:
    obj = await db.get(Branch, eid)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Filial topilmadi")
    return obj


@router.get("", response_model=list[BranchStats])
async def list_branches(db: Db, _: Admin) -> list[dict]:
    """Filiallar + har birida hududlar, kameralar va onlayn kameralar soni (2 ta yig'ma so'rov)."""
    ents = (await db.execute(select(Branch).order_by(Branch.name))).scalars().all()
    facs = dict((await db.execute(
        select(Zone.branch_id, func.count()).group_by(Zone.branch_id))).all())
    cams = {eid: (n, on) for eid, n, on in (await db.execute(
        select(Zone.branch_id, func.count(Camera.id),
               func.count(Camera.id).filter(Camera.status == "online"))
        .join(Camera, Camera.zone_id == Zone.id)
        .group_by(Zone.branch_id))).all()}
    out = []
    for e in ents:
        n, on = cams.get(e.id, (0, 0))
        out.append({**BranchOut.model_validate(e).model_dump(),
                    "zones": facs.get(e.id, 0), "cameras": n, "online": on})
    return out


@router.get("/unassigned")
async def unassigned(db: Db, _: Admin) -> dict:
    """Hech qaysi hududga biriktirilmagan kameralar soni (filial sahifasida alohida karta)."""
    n, on = (await db.execute(
        select(func.count(Camera.id), func.count(Camera.id).filter(Camera.status == "online"))
        .where(Camera.zone_id.is_(None)))).one()
    return {"cameras": n, "online": on}


@router.post("", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
async def create_branch(payload: BranchCreate, db: Db, _: Admin) -> Branch:
    obj = Branch(name=payload.name, code=payload.code)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{eid}", response_model=BranchOut)
async def update_branch(eid: uuid.UUID, payload: BranchUpdate, db: Db, _: Admin) -> Branch:
    obj = await _get_or_404(db, eid)
    apply_patch(obj, payload.model_dump(exclude_unset=True))
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{eid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_branch(eid: uuid.UUID, db: Db, _: Admin) -> None:
    obj = await _get_or_404(db, eid)
    # Filial o'chsa hududlari ham o'chadi (DB CASCADE), kameralar esa
    # biriktirilmagan bo'lib qoladi (zone_id SET NULL) — o'chmaydi.
    await db.delete(obj)
    await db.commit()
