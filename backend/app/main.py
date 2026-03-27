import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, and_

from app.database import AsyncSessionLocal
from app.models import Detection
from app.routers import cameras, detections, archive, analytics, agent
from app.worker import register_ws, unregister_ws, register_alert_ws, unregister_alert_ws

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

scheduler = AsyncIOScheduler()

TRASH_RETENTION_DAYS = 30


async def daily_trash_purge():
    """Daily APScheduler job: permanently delete TRASH items older than 30 days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRASH_RETENTION_DAYS)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Detection).where(
                and_(
                    Detection.status == "TRASH",
                    Detection.reviewed_at <= cutoff,
                )
            ).returning(Detection.id)
        )
        deleted = result.scalars().all()
        await session.commit()
        logger.info(f"Daily trash purge: deleted {len(deleted)} TRASH detections older than {TRASH_RETENTION_DAYS} days")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(daily_trash_purge, "cron", hour=3, minute=0)
    scheduler.start()
    logger.info("APScheduler started — daily trash purge scheduled (30-day retention)")
    
    # Auto-start active cameras
    from sqlalchemy import select
    from app.models import Camera
    from app.worker import start_worker
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Camera).where(Camera.is_active == True))
        cameras = result.scalars().all()
        loop = asyncio.get_running_loop()
        for cam in cameras:
            start_worker(cam.id, cam.name, cam.stream_url, cam.location_name or "", loop)
        logger.info(f"Auto-started {len(cameras)} active cameras")

    yield
    scheduler.shutdown()
    logger.info("APScheduler shutdown")


app = FastAPI(
    title="SEVAT",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(cameras.router)
app.include_router(detections.router)
app.include_router(archive.router)
app.include_router(analytics.router)
app.include_router(agent.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


# WebSocket endpoint
@app.websocket("/ws/stream/{camera_id}")
async def ws_stream(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=30)

    await register_ws(camera_id, queue)
    try:
        while True:
            try:
                frame_bytes = await asyncio.wait_for(queue.get(), timeout=5.0)
                await websocket.send_bytes(frame_bytes)
            except asyncio.TimeoutError:
                # Send a keepalive ping
                try:
                    await websocket.send_text("ping")
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for camera {camera_id}")
    except Exception as e:
        logger.warning(f"WebSocket error for camera {camera_id}: {e}")
    finally:
        await unregister_ws(camera_id, queue)


# Alert WebSocket endpoint
@app.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    """Push detection alerts to operators in real time."""
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    await register_alert_ws(queue)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=10.0)
                await websocket.send_text(msg)
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("Alert WebSocket disconnected")
    except Exception as e:
        logger.warning(f"Alert WebSocket error: {e}")
    finally:
        await unregister_alert_ws(queue)
