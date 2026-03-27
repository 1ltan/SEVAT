import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Detection, Camera
from app.schemas import APIResponse, DetectionOut, DetectionPatch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/detections", tags=["detections"])


@router.get("", response_model=APIResponse)
async def list_detections(
    status: Optional[str] = Query(None),
    camera_id: Optional[int] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    class_name: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if status:
        filters.append(Detection.status == status)
    if camera_id:
        filters.append(Detection.camera_id == camera_id)
    if date_from:
        filters.append(Detection.detected_at >= date_from)
    if date_to:
        filters.append(Detection.detected_at <= date_to)
    if class_name:
        filters.append(Detection.class_name == class_name)

    stmt = (
        select(Detection, Camera.name.label("cam_name"), Camera.location_name.label("cam_loc"))
        .join(Camera, Camera.id == Detection.camera_id)
        .where(and_(*filters) if filters else True)
        .order_by(Detection.detected_at.desc())
        .limit(500)
    )
    result = await db.execute(stmt)
    rows = result.all()

    data = []
    for row in rows:
        d = row[0]
        item = {
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
            "operator_id": d.operator_id,
            "reviewed_at": d.reviewed_at,
            "threat_level": d.threat_level,
            "threat_reasoning": d.threat_reasoning,
        }
        data.append(item)
    return APIResponse(data=data)


@router.patch("/{detection_id}", response_model=APIResponse)
async def patch_detection(detection_id: int, body: DetectionPatch, db: AsyncSession = Depends(get_db)):
    det = await db.get(Detection, detection_id)
    if not det:
        raise HTTPException(status_code=404, detail="Detection not found")

    now = datetime.utcnow()
    if body.action == "confirm":
        det.status = "CONFIRMED"
        det.reviewed_at = now
    elif body.action == "reject":
        det.status = "TRASH"
        det.reviewed_at = now
    elif body.action == "correct":
        det.status = "CONFIRMED"
        det.operator_correction = body.correction
        det.reviewed_at = now
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    await db.commit()
    await db.refresh(det)
    return APIResponse(data={"id": det.id, "status": det.status})


@router.get("/{detection_id}/screenshot")
async def get_screenshot(detection_id: int, db: AsyncSession = Depends(get_db)):
    det = await db.get(Detection, detection_id)
    if not det or not det.screenshot_path:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    if not os.path.exists(det.screenshot_path):
        raise HTTPException(status_code=404, detail="Screenshot file missing")
    return FileResponse(det.screenshot_path, media_type="image/jpeg")
