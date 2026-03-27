import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Detection, Camera
from app.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["archive"])


@router.get("/api/archive", response_model=APIResponse)
async def get_archive(
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    camera_id: Optional[int] = Query(None),
    location: Optional[str] = Query(None),
    class_name: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    filters = [Detection.status.in_(["CONFIRMED", "ARCHIVED"])]
    if date_from:
        filters.append(Detection.detected_at >= date_from)
    if date_to:
        filters.append(Detection.detected_at <= date_to)
    if camera_id:
        filters.append(Detection.camera_id == camera_id)
    if location:
        filters.append(Camera.location_name.ilike(f"%{location}%"))
    if class_name:
        filters.append(Detection.class_name == class_name)

    stmt = (
        select(Detection, Camera.name.label("cam_name"), Camera.location_name.label("cam_loc"))
        .join(Camera, Camera.id == Detection.camera_id)
        .where(and_(*filters))
        .order_by(Detection.detected_at.desc())
        .limit(1000)
    )
    result = await db.execute(stmt)
    rows = result.all()
    data = [_row_to_dict(r) for r in rows]
    return APIResponse(data=data)


@router.get("/api/trash", response_model=APIResponse)
async def get_trash(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Detection, Camera.name.label("cam_name"), Camera.location_name.label("cam_loc"))
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Detection.status == "TRASH")
        .order_by(Detection.detected_at.desc())
        .limit(1000)
    )
    result = await db.execute(stmt)
    rows = result.all()
    return APIResponse(data=[_row_to_dict(r) for r in rows])


@router.delete("/api/trash/purge", response_model=APIResponse)
async def purge_trash(db: AsyncSession = Depends(get_db)):
    """Permanently delete ALL items currently in trash."""
    result = await db.execute(
        delete(Detection).where(Detection.status == "TRASH").returning(Detection.id)
    )
    deleted_ids = result.scalars().all()
    await db.commit()
    return APIResponse(data={"deleted": len(deleted_ids)})


@router.post("/api/trash/{detection_id}/restore", response_model=APIResponse)
async def restore_from_trash(detection_id: int, db: AsyncSession = Depends(get_db)):
    """Restore a single detection from TRASH back to PENDING."""
    det = await db.get(Detection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="Detection not found")
    if det.status != "TRASH":
        raise HTTPException(status_code=400, detail="Detection is not in trash")
    det.status = "PENDING"
    det.reviewed_at = None
    det.operator_id = None
    await db.commit()
    await db.refresh(det)
    return APIResponse(data={"id": det.id, "status": det.status})


def _row_to_dict(row) -> dict:
    d = row[0]
    return {
        "id": d.id,
        "camera_id": d.camera_id,
        "camera_name": row[1],
        "camera_location": row[2],
        "detected_at": d.detected_at,
        "class_name": d.class_name,
        "confidence": d.confidence,
        "screenshot_path": d.screenshot_path,
        "status": d.status,
        "operator_correction": d.operator_correction,
        "reviewed_at": d.reviewed_at,
        "threat_level": d.threat_level,
        "threat_reasoning": d.threat_reasoning,
    }
