import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Camera
from app.schemas import APIResponse, CameraCreate, CameraOut, CameraUpdate
from app.worker import get_workers, start_worker, stop_worker

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _enrich(cam: Camera) -> dict:
    workers = get_workers()
    data = {
        "id": cam.id,
        "name": cam.name,
        "stream_url": cam.stream_url,
        "location_name": cam.location_name,
        "latitude": cam.latitude,
        "longitude": cam.longitude,
        "added_at": cam.added_at,
        "is_active": cam.is_active,
        "is_running": cam.id in workers and workers[cam.id].is_running,
    }
    return data


@router.post("", response_model=APIResponse)
async def add_camera(body: CameraCreate, db: AsyncSession = Depends(get_db)):
    cam = Camera(**body.model_dump())
    db.add(cam)
    await db.commit()
    await db.refresh(cam)
    loop = asyncio.get_event_loop()
    start_worker(cam.id, cam.name, cam.stream_url, cam.location_name or "", loop)
    return APIResponse(data=_enrich(cam))


@router.get("", response_model=APIResponse)
async def list_cameras(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Camera).order_by(Camera.id))
    cameras = result.scalars().all()
    return APIResponse(data=[_enrich(c) for c in cameras])


@router.delete("/{camera_id}", response_model=APIResponse)
async def delete_camera(camera_id: int, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    stop_worker(camera_id)
    await db.delete(cam)
    await db.commit()
    return APIResponse(data={"id": camera_id})


@router.patch("/{camera_id}", response_model=APIResponse)
async def update_camera(camera_id: int, body: CameraUpdate, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(cam, k, v)
    await db.commit()
    await db.refresh(cam)
    return APIResponse(data=_enrich(cam))


@router.post("/{camera_id}/start", response_model=APIResponse)
async def start_camera(camera_id: int, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    loop = asyncio.get_event_loop()
    start_worker(camera_id, cam.name, cam.stream_url, cam.location_name or "", loop)
    return APIResponse(data={"camera_id": camera_id, "status": "started"})


@router.post("/{camera_id}/stop", response_model=APIResponse)
async def stop_camera(camera_id: int, db: AsyncSession = Depends(get_db)):
    cam = await db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    stop_worker(camera_id)
    return APIResponse(data={"camera_id": camera_id, "status": "stopped"})
