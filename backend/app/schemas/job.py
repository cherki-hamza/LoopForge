from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class JobBase(BaseModel):
    filename: str
    target_seconds: float
    mode: str = "fast"


class JobResponse(BaseModel):
    id: str
    filename: str
    file_size_bytes: int
    target_seconds: float
    input_duration_seconds: float
    output_duration_seconds: Optional[float] = None
    mode: str
    actual_mode_used: Optional[str] = None
    status: str
    progress: float
    eta_seconds: Optional[float] = None
    error_message: Optional[str] = None
    queue_position: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class JobListResponse(BaseModel):
    jobs: List[JobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class BatchDeleteRequest(BaseModel):
    job_ids: List[str]


class BatchDeleteResponse(BaseModel):
    deleted_ids: List[str]
    failed_ids: List[str]
