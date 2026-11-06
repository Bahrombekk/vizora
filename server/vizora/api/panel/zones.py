"""Hududlar CRUD — filialga tegishli ixtiyoriy guruh."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vizora.core.db import get_db
from vizora.api.patch import apply_patch, reject_null_columns
from vizora.api.deps import CurrentUser, require_role
from vizora.models.camera import Camera
from vizora.models.branch import Branch
from vizora.models.zone import Zone
from vizora.schemas.zone import ZoneCreate, ZoneOut, ZoneStats, ZoneUpdate

router = APIRouter(prefix="/zones", tags=["zones"])

Admin = Annotated[CurrentUser, Depends(require_role("admin", "operator"))]
Db = Annotated[AsyncSession, Depends(get_db)]


async def _get_or_404(db: AsyncSession, fid: uuid.UUID) -> Zone:
    obj = await db.get(Zone, fid)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Hudud topilmadi")
    return obj


async def _check_branch(db: AsyncSession, eid: uuid.UUID) -> None:
    if await db.get(Branch, eid) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Filial mavjud emas")


@router.get("", response_model=list[ZoneStats])
async def list_zones(
    db: Db, _: Admin, branch_id: uuid.UUID | None = None
) -> list[dict]:
    """Hududlar + har birida kameralar va onlayn kameralar soni (bitta yig'ma so'rov)."""
    stmt = select(Zone).order_by(Zone.name)
    if branch_id is not None:
        stmt = stmt.where(Zone.branch_id == branch_id)
    facs = (await db.execute(stmt)).scalars().all()
    cams = {fid: (n, on) for fid, n, on in (await db.execute(
        select(Camera.zone_id, func.count(), func.count().filter(Camera.status == "online"))
        .where(Camera.zone_id.is_not(None)).group_by(Camera.zone_id))).all()}
    return [
        {**ZoneOut.model_validate(f).model_dump(), "cameras": cams.get(f.id, (0, 0))[0],
         "online": cams.get(f.id, (0, 0))[1]}
        for f in facs
    ]


@router.post("", response_model=ZoneOut, status_code=status.HTTP_201_CREATED)
async def create_zone(payload: ZoneCreate, db: Db, _: Admin) -> Zone:
    await _check_branch(db, payload.branch_id)
    obj = Zone(
        branch_id=payload.branch_id,
        name=payload.name,
        code=payload.code,
        location=payload.location or None,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.patch("/{fid}", response_model=ZoneOut)
async def update_zone(fid: uuid.UUID, payload: ZoneUpdate, db: Db, _: Admin) -> Zone:
    obj = await _get_or_404(db, fid)
    data = payload.model_dump(exclude_unset=True)
    reject_null_columns(obj, data)
    if "branch_id" in data:
        await _check_branch(db, data["branch_id"])
    apply_patch(obj, data)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/{fid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(fid: uuid.UUID, db: Db, _: Admin) -> None:
    obj = await _get_or_404(db, fid)
    # Hudud o'chsa kameralari o'chmaydi — zone_id SET NULL bo'ladi.
    await db.delete(obj)
    await db.commit()
