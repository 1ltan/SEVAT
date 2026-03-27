"""
Camera detection worker — one thread per camera.
YOLO model is loaded ONCE at worker start, never per frame.
"""
import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

import cv2
import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# ── Ukrainian class labels ──────────────────────────────────────────────────
CLASS_LABELS_UA: Dict[str, str] = {
    "APC":     "БТР",      # manual correction class / backward compat
    "IFV":     "БМП",      # normalized name saved to DB for model's APC-IFV
    "APC-IFV": "БМП",      # raw model output — kept for display of old records
    "TANK":    "Танк",
    "CAR":     "Автомобіль",
    "TRUCK":   "Вантажівка",
    "ART":     "Артилерія",
    "MLRS":    "РСЗВ",
}
KNOWN_CLASSES: Set[str] = set(CLASS_LABELS_UA.keys())

# ── Normalize raw model class → clean DB class ───────────────────────────────
# The trained model may output compound names; we normalize before saving to DB
CLASS_NORMALIZE: Dict[str, str] = {
    "APC-IFV": "IFV",  # model output → clean class name stored in DB
}

# ── Worker registry ─────────────────────────────────────────────────────────
_workers: Dict[int, "CameraWorker"] = {}
_workers_lock = threading.Lock()

# ── WebSocket subscriber registry (video frames) ────────────────────────────
_ws_subscribers: Dict[int, Set] = {}
_ws_lock = asyncio.Lock()

# ── Alert subscriber registry (operator notifications) ──────────────────────
_alert_subscribers: Set = set()
_alert_lock = asyncio.Lock()

# ── Shared DB engine (created once, reused across detections) ───────────────
_db_engine = None
_db_session_factory = None
_db_engine_lock = threading.Lock()


def _get_db_session_factory():
    global _db_engine, _db_session_factory
    with _db_engine_lock:
        if _db_engine is None:
            _db_engine = create_async_engine(
                settings.database_url, echo=False,
                pool_size=5, max_overflow=10
            )
            _db_session_factory = async_sessionmaker(_db_engine, expire_on_commit=False)
    return _db_session_factory


# ── Public API ───────────────────────────────────────────────────────────────

def get_workers() -> Dict[int, "CameraWorker"]:
    return _workers


async def register_ws(camera_id: int, queue):
    async with _ws_lock:
        _ws_subscribers.setdefault(camera_id, set()).add(queue)


async def unregister_ws(camera_id: int, queue):
    async with _ws_lock:
        if camera_id in _ws_subscribers:
            _ws_subscribers[camera_id].discard(queue)


async def broadcast_frame(camera_id: int, frame_bytes: bytes):
    async with _ws_lock:
        subs = list(_ws_subscribers.get(camera_id, set()))
    for q in subs:
        try:
            q.put_nowait(frame_bytes)
        except Exception:
            pass


async def register_alert_ws(queue):
    """Register a WebSocket queue for operator alert notifications."""
    async with _alert_lock:
        _alert_subscribers.add(queue)


async def unregister_alert_ws(queue):
    """Unregister a WebSocket queue for operator alert notifications."""
    async with _alert_lock:
        _alert_subscribers.discard(queue)


async def broadcast_alert(alert_data: dict):
    """Broadcast a detection alert to all connected operator WebSockets."""
    async with _alert_lock:
        subs = list(_alert_subscribers)
    msg = json.dumps(alert_data, ensure_ascii=False, default=str)
    for q in subs:
        try:
            q.put_nowait(msg)
        except Exception:
            pass


# Мінімальний інтервал між сповіщеннями про один і той самий клас об'єкта (секунди)
DETECTION_COOLDOWN_SECONDS = 30


class CameraWorker:
    def __init__(self, camera_id: int, camera_name: str, stream_url: str, location_name: str):
        self.camera_id = camera_id
        self.camera_name = camera_name
        self.stream_url = stream_url
        self.location_name = location_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Cooldown: зберігає час останнього збереженого виявлення для кожного класу
        self._last_detection_time: Dict[str, float] = {}

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"worker-cam-{self.camera_id}")
        self._thread.start()
        logger.info(f"Worker started for camera {self.camera_id}")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"Worker stopped for camera {self.camera_id}")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _open_capture(self) -> Optional[cv2.VideoCapture]:
        """Open video capture with IP WebCam / MJPEG / RTSP support."""
        url = self.stream_url

        # For HTTP MJPEG streams (IP WebCam), try with FFMPEG backend first
        if url.startswith("http"):
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(url)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FPS, settings.stream_fps)
                return cap
            cap.release()
            return None

        # RTSP / other streams
        cap = cv2.VideoCapture(url)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        cap.release()
        return None

    def _run(self):
        # Try to load YOLO model — but DO NOT stop streaming if it fails
        model = None
        try:
            import torch
            import functools

            # PyTorch ≥2.6 changed default weights_only to True which breaks
            # ultralytics YOLO models (contains custom classes like Sequential,
            # DetectionModel, etc.). We patch torch.load globally within this
            # thread to force weights_only=False for our trusted local model file.
            _orig_torch_load = torch.load

            @functools.wraps(_orig_torch_load)
            def _patched_torch_load(f, *args, **kwargs):
                kwargs.setdefault("weights_only", False)
                return _orig_torch_load(f, *args, **kwargs)

            torch.load = _patched_torch_load

            from ultralytics import YOLO

            model_path = settings.model_path
            if not Path(model_path).exists():
                logger.error(
                    f"Camera {self.camera_id}: YOLO model file NOT FOUND at '{model_path}'. "
                    "Streaming raw frames without detection. Fix MODEL_PATH in .env"
                )
            else:
                model = YOLO(model_path)
                logger.info(
                    f"YOLO model loaded for camera {self.camera_id} from '{model_path}'. "
                    f"Classes: {list(model.names.values())}"
                )

            # Restore original torch.load after model is loaded
            torch.load = _orig_torch_load

        except Exception as e:
            logger.warning(
                f"Camera {self.camera_id}: YOLO model not available ({e}). "
                "Streaming raw frames without detection."
            )

        frame_interval = 1.0 / settings.stream_fps
        last_broadcast = 0.0
        frame_count = 0

        while not self._stop_event.is_set():
            cap = self._open_capture()
            if cap is None:
                logger.warning(
                    f"Cannot open stream for camera {self.camera_id} "
                    f"(url={self.stream_url}), retrying in 5s"
                )
                time.sleep(5)
                continue

            logger.info(f"Stream opened for camera {self.camera_id}: {self.stream_url}")
            try:
                while not self._stop_event.is_set():
                    ret, frame = cap.read()
                    if not ret:
                        logger.warning(f"Frame read failed for camera {self.camera_id}, reconnecting")
                        break

                    frame_count += 1
                    if frame_count % settings.frame_skip != 0:
                        continue

                    # Run detection if model available, else broadcast raw frame
                    if model is not None:
                        annotated = self._process_frame(model, frame)
                    else:
                        annotated = frame

                    now = time.monotonic()
                    if now - last_broadcast >= frame_interval:
                        last_broadcast = now
                        _, buf = cv2.imencode(
                            ".jpg", annotated,
                            [cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality]
                        )
                        frame_bytes = buf.tobytes()
                        if self._loop and not self._loop.is_closed():
                            asyncio.run_coroutine_threadsafe(
                                broadcast_frame(self.camera_id, frame_bytes),
                                self._loop
                            )

            finally:
                cap.release()

    def _process_frame(self, model, frame: np.ndarray) -> np.ndarray:
        results = model(frame, verbose=False)
        annotated = frame.copy()
        now = time.monotonic()

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                confidence = float(box.conf[0])
                if confidence <= 0.20:
                    continue

                class_idx = int(box.cls[0])
                raw_class = model.names.get(class_idx, "")
                if raw_class not in KNOWN_CLASSES:
                    if raw_class:
                        logger.debug(f"Unknown class '{raw_class}' — skipping")
                    continue

                # Normalize raw model class to clean DB class (e.g. APC-IFV → IFV)
                db_class = CLASS_NORMALIZE.get(raw_class, raw_class)
                label_ua = CLASS_LABELS_UA[raw_class]
                x1, y1, x2, y2 = map(int, box.xyxy[0])

                # Draw bounding box (завжди, незалежно від cooldown)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), 2)
                label_text = f"{int(confidence * 100)}% - {db_class}"
                cv2.putText(annotated, label_text, (x1, y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

                # Cooldown-перевірка: використовуємо нормалізований клас
                last_time = self._last_detection_time.get(db_class, 0.0)
                if now - last_time < DETECTION_COOLDOWN_SECONDS:
                    logger.debug(
                        f"Camera {self.camera_id}: cooldown active for '{db_class}' "
                        f"({DETECTION_COOLDOWN_SECONDS - (now - last_time):.1f}s залишилось)"
                    )
                    continue

                # Оновлюємо час останнього виявлення (нормалізований клас)
                self._last_detection_time[db_class] = now

                # Save screenshot
                ts = datetime.now(timezone.utc)
                screenshot_path = self._save_screenshot(annotated, ts)

                # Persist detection asynchronously (зберігаємо нормалізований клас)
                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._persist_detection(db_class, label_ua, confidence, screenshot_path, ts),
                        self._loop
                    )

        return annotated

    def _save_screenshot(self, frame: np.ndarray, ts: datetime) -> Optional[str]:
        try:
            cam_dir = Path(settings.screenshot_dir) / str(self.camera_id)
            cam_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{ts.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            path = cam_dir / filename
            cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality])
            return str(path)
        except Exception as e:
            logger.error(f"Screenshot save failed: {e}")
            return None

    async def _persist_detection(
        self, raw_class: str, label_ua: str, confidence: float,
        screenshot_path: Optional[str], detected_at: datetime
    ):
        # Determine status via confidence routing
        if confidence >= 0.70:
            status = "ARCHIVED"
        elif confidence >= 0.21:
            status = "PENDING"
        else:
            status = "TRASH"

        session_factory = _get_db_session_factory()

        async with session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                        INSERT INTO detections
                            (camera_id, detected_at, class_name, confidence, screenshot_path, status)
                        VALUES
                            (:camera_id, :detected_at, :class_name, :confidence, :screenshot_path, :status)
                        RETURNING id
                    """),
                    {
                        "camera_id": self.camera_id,
                        "detected_at": detected_at,
                        "class_name": raw_class,
                        "confidence": confidence,
                        "screenshot_path": screenshot_path,
                        "status": status,
                    }
                )
                detection_id = result.scalar_one()

        logger.info(
            f"Detection saved: id={detection_id} class={raw_class} "
            f"conf={confidence:.2f} status={status} cam={self.camera_id}"
        )

        # Broadcast alert to operator WebSockets (for PENDING and ARCHIVED)
        if status in ("PENDING", "ARCHIVED"):
            alert_payload = {
                "type": "detection",
                "detection_id": detection_id,
                "camera_id": self.camera_id,
                "camera_name": self.camera_name,
                "camera_location": self.location_name,
                "class_name": raw_class,
                "class_name_ua": label_ua,
                "confidence": round(confidence, 4),
                "confidence_pct": int(confidence * 100),
                "status": status,
                "detected_at": detected_at.isoformat(),
                "screenshot_path": screenshot_path,
            }
            await broadcast_alert(alert_payload)

        # For confirmed/archived detections, generate embedding
        if status in ("ARCHIVED", "CONFIRMED"):
            await self._index_embedding(detection_id, label_ua, detected_at)

        # Auto-trigger threat analysis
        if status != "TRASH":
            await self._check_threat_escalation(detection_id, raw_class)

    async def _index_embedding(self, detection_id: int, label_ua: str, detected_at: datetime):
        try:
            from app.agent.graph import embed_text
            text_to_embed = (
                f"{label_ua} на камері {self.camera_name}, "
                f"локація {self.location_name}, "
                f"час {detected_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            vector = await embed_text(text_to_embed)
            if not vector:
                return
            vector_str = "[" + ",".join(str(v) for v in vector) + "]"
            session_factory = _get_db_session_factory()
            async with session_factory() as session:
                async with session.begin():
                    await session.execute(
                        text("INSERT INTO agent_embeddings (detection_id, embedding) VALUES (:did, :emb::vector)"),
                        {"did": detection_id, "emb": vector_str}
                    )
        except Exception as e:
            logger.error(f"Embedding indexing failed: {e}")

    async def _check_threat_escalation(self, detection_id: int, class_name: str):
        """Auto-trigger threat analysis per escalation rules."""
        try:
            session_factory = _get_db_session_factory()
            async with session_factory() as session:
                # Rule 1: 3+ same class same camera in 10 min → CRITICAL
                result = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM detections
                        WHERE camera_id = :cid
                          AND class_name = :cls
                          AND status != 'TRASH'
                          AND detected_at >= NOW() - INTERVAL '10 minutes'
                    """),
                    {"cid": self.camera_id, "cls": class_name}
                )
                same_class_count = result.scalar()

                threat_level = None
                reasoning = None

                if same_class_count >= 3:
                    threat_level = "CRITICAL"
                    reasoning = (
                        f"{same_class_count} виявлень класу '{class_name}' "
                        f"на камері '{self.camera_name}' за останні 10 хвилин."
                    )
                else:
                    # Rule 2: Multiple classes at same location in 5 min → HIGH
                    result2 = await session.execute(
                        text("""
                            SELECT COUNT(DISTINCT class_name) FROM detections d
                            JOIN cameras c ON c.id = d.camera_id
                            WHERE c.location_name = (SELECT location_name FROM cameras WHERE id = :cid)
                              AND d.status != 'TRASH'
                              AND d.detected_at >= NOW() - INTERVAL '5 minutes'
                        """),
                        {"cid": self.camera_id}
                    )
                    distinct_classes = result2.scalar()
                    if distinct_classes >= 2:
                        threat_level = "HIGH"
                        reasoning = (
                            f"{distinct_classes} різних класів загроз зафіксовано "
                            f"на локації '{self.location_name}' за останні 5 хвилин."
                        )

                if threat_level:
                    async with session.begin():
                        await session.execute(
                            text("""
                                UPDATE detections
                                SET threat_level = :lvl, threat_reasoning = :rsn
                                WHERE id = :did
                            """),
                            {"lvl": threat_level, "rsn": reasoning, "did": detection_id}
                        )

        except Exception as e:
            logger.error(f"Threat escalation check failed: {e}")


# ── Public API ───────────────────────────────────────────────────────────────

def start_worker(camera_id: int, camera_name: str, stream_url: str,
                 location_name: str, loop: asyncio.AbstractEventLoop):
    with _workers_lock:
        if camera_id in _workers and _workers[camera_id].is_running:
            return
        worker = CameraWorker(camera_id, camera_name, stream_url, location_name)
        worker.start(loop)
        _workers[camera_id] = worker


def stop_worker(camera_id: int):
    with _workers_lock:
        worker = _workers.pop(camera_id, None)
    if worker:
        worker.stop()
