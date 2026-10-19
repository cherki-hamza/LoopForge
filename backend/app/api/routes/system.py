from fastapi import APIRouter, Depends
from sqlalchemy import text, func
from sqlalchemy.orm import Session
import redis
from app.core.config import settings
from app.db.session import get_db
from app.db.models import Job, JobStatus
from app.services.storage import get_disk_usage
from app.schemas.system import SystemStatsResponse, DiskStats, JobCounts

router = APIRouter(tags=["system"])


@router.get("/system", response_model=SystemStatsResponse)
def get_system_stats(db: Session = Depends(get_db)):
    """Return disk space and job queue statistics."""
    total_bytes, used_bytes, free_bytes = get_disk_usage()
    percent_used = (used_bytes / total_bytes * 100) if total_bytes > 0 else 0.0

    disk = DiskStats(
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        free_gb=round(free_bytes / (1024 ** 3), 2),
        total_gb=round(total_bytes / (1024 ** 3), 2),
        percent_used=round(percent_used, 1),
    )

    counts = (
        db.query(Job.status, func.count(Job.id))
        .group_by(Job.status)
        .all()
    )
    counts_dict = {status: count for status, count in counts}

    jobs = JobCounts(
        active=counts_dict.get(JobStatus.PROCESSING.value, 0),
        queued=counts_dict.get(JobStatus.QUEUED.value, 0),
        completed=counts_dict.get(JobStatus.COMPLETED.value, 0),
        failed=counts_dict.get(JobStatus.FAILED.value, 0),
        cancelled=counts_dict.get(JobStatus.CANCELLED.value, 0),
        total=sum(counts_dict.values()),
    )

    return SystemStatsResponse(
        disk=disk,
        jobs=jobs,
        max_file_size_mb=settings.MAX_FILE_SIZE_MB,
        max_clip_duration_seconds=settings.MAX_CLIP_DURATION_SECONDS,
    )


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Health check endpoint for Docker Compose and monitoring."""
    status_report = {"status": "healthy", "database": "ok", "redis": "ok"}

    # DB check
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        status_report["database"] = f"error: {str(e)}"
        status_report["status"] = "degraded"

    # Redis check
    try:
        r = redis.from_url(settings.REDIS_URL, socket_timeout=2)
        r.ping()
    except Exception as e:
        status_report["redis"] = f"error: {str(e)}"
        status_report["status"] = "degraded"

    return status_report
