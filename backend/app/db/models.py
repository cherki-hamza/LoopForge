import enum
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Text,
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobMode(str, enum.Enum):
    FAST = "fast"
    SAFE = "safe"


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid, index=True)
    filename = Column(String(255), nullable=False)
    input_path = Column(String(1024), nullable=False)
    output_path = Column(String(1024), nullable=True)
    file_size_bytes = Column(Integer, nullable=False, default=0)
    target_seconds = Column(Float, nullable=False)
    input_duration_seconds = Column(Float, nullable=False, default=0.0)
    output_duration_seconds = Column(Float, nullable=True)
    mode = Column(String(20), nullable=False, default=JobMode.FAST.value)
    actual_mode_used = Column(String(20), nullable=True)
    
    status = Column(
        String(20),
        nullable=False,
        default=JobStatus.QUEUED.value,
        index=True
    )
    progress = Column(Float, nullable=False, default=0.0)
    eta_seconds = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    
    celery_task_id = Column(String(64), nullable=True, index=True)
    process_pid = Column(Integer, nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("idx_jobs_status_created", "status", "created_at"),
    )
