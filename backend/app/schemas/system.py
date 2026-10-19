from pydantic import BaseModel


class DiskStats(BaseModel):
    free_bytes: int
    total_bytes: int
    used_bytes: int
    free_gb: float
    total_gb: float
    percent_used: float


class JobCounts(BaseModel):
    active: int
    queued: int
    completed: int
    failed: int
    cancelled: int
    total: int


class SystemStatsResponse(BaseModel):
    disk: DiskStats
    jobs: JobCounts
    max_file_size_mb: int
    max_clip_duration_seconds: float
