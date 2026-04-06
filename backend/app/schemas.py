from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


# Responses 
class APIResponse(BaseModel):
    success: bool = True
    data: Any = None
    error: Optional[str] = None


# Cameras
class CameraCreate(BaseModel):
    name: str
    stream_url: str
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    stream_url: Optional[str] = None
    location_name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: Optional[bool] = None


class CameraOut(BaseModel):
    id: int
    name: str
    stream_url: str
    location_name: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    added_at: datetime
    is_active: bool
    is_running: bool = False

    class Config:
        from_attributes = True


# Detections
class DetectionOut(BaseModel):
    id: int
    camera_id: int
    camera_name: Optional[str] = None
    camera_location: Optional[str] = None
    detected_at: datetime
    class_name: str
    confidence: float
    screenshot_path: Optional[str]
    status: str
    operator_correction: Optional[str]
    operator_id: Optional[int]
    reviewed_at: Optional[datetime]
    threat_level: Optional[str]
    threat_reasoning: Optional[str]

    class Config:
        from_attributes = True


class DetectionPatch(BaseModel):
    action: str
    correction: Optional[str] = None


class DetectionFilter(BaseModel):
    status: Optional[str] = None
    camera_id: Optional[int] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    class_name: Optional[str] = None


# Analytics
class ReportRequest(BaseModel):
    date_from: datetime
    date_to: datetime
    camera_ids: Optional[List[int]] = None
    class_names: Optional[List[str]] = None
    group_by: str = "day"



# Agent
class ChatRequest(BaseModel):
    session_id: str
    message: str


class AgentMessageOut(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
