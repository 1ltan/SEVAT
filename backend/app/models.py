from datetime import datetime
from typing import Optional
import uuid as _uuid
from sqlalchemy import (
    Boolean, Column, Float, ForeignKey, Integer, String, Text, DateTime
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database import Base


class Operator(Base):
    __tablename__ = "operators"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(50), nullable=False, default="operator")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    detections = relationship("Detection", back_populates="operator")


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    stream_url = Column(Text, nullable=False)
    location_name = Column(String(200))
    latitude = Column(Float)
    longitude = Column(Float)
    added_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    detections = relationship("Detection", back_populates="camera", cascade="all, delete-orphan")


class Detection(Base):
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    detected_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    class_name = Column(String(100), nullable=False)
    confidence = Column(Float, nullable=False)
    screenshot_path = Column(Text)
    status = Column(String(20), nullable=False, default="PENDING")
    operator_correction = Column(String(100))
    operator_id = Column(Integer, ForeignKey("operators.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    threat_level = Column(String(20), nullable=True)
    threat_reasoning = Column(Text, nullable=True)

    camera = relationship("Camera", back_populates="detections")
    operator = relationship("Operator", back_populates="detections")
    embedding = relationship("AgentEmbedding", back_populates="detection", uselist=False, cascade="all, delete-orphan")


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class AgentEmbedding(Base):
    __tablename__ = "agent_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    detection_id = Column(Integer, ForeignKey("detections.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    detection = relationship("Detection", back_populates="embedding")
