import os
import uuid
import re
from pathlib import Path
from typing import Optional, List
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    UploadFile,
    File,
    Form,
    Request,
    Response,
    status,
    Query,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.core.config import settings
from app.core.celery_app import celery_app
from app.db.session import get_db
from app.db.models import Job, JobStatus, JobMode
from app.schemas.job import (
    JobResponse,
    JobListResponse,
    BatchDeleteRequest,
    BatchDeleteResponse,
)
from app.api.deps import get_current_admin
from app.services.ffmpeg_pipeline import (
    probe_media,
    validate_clip,
    kill_process_group,
    FFmpegPipelineError,
)
from app.services.storage import (
    stream_upload_to_disk,
    estimate_output_size,
    check_disk_space_available,
    get_disk_usage,
    parse_range_header,
    range_file_iterator,
)
from app.tasks.process_job import process_video_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


def sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and special chars."""
    basename = os.path.basename(filename)
    clean = re.sub(r"[^a-zA-Z0-9_.-]", "_", basename)
    return clean[:120] if clean else "clip.mp4"


def compute_queue_position(db: Session, job: Job) -> Optional[int]:
    """Compute 1-indexed FIFO queue position for a queued job."""
    if job.status != JobStatus.QUEUED.value:
        return None
    pos = (
        db.query(func.count(Job.id))
        .filter(
            Job.status == JobStatus.QUEUED.value,
            Job.created_at <= job.created_at,
        )
        .scalar()
    )
    return pos


def map_to_response(db: Session, job: Job) -> JobResponse:
    resp = JobResponse.model_validate(job)
    resp.queue_position = compute_queue_position(db, job)
    return resp


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    file: UploadFile = File(...),
    target_seconds: float = Form(...),
    mode: str = Form("fast"),
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """
    Upload a video clip and enqueue a looping job.
    Streams directly to disk in chunks. Validates video/audio with ffprobe.
    """
    if target_seconds < 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target duration must be at least 10 seconds."
        )

    mode_clean = mode.lower().strip()
    if mode_clean not in (JobMode.FAST.value, JobMode.SAFE.value):
        mode_clean = JobMode.FAST.value

    job_id = str(uuid.uuid4())
    safe_name = sanitize_filename(file.filename or "video.mp4")
    upload_filename = f"{job_id}_{safe_name}"
    upload_path = Path(settings.uploads_dir) / upload_filename

    # Stream upload to disk in chunks (never hold whole file in memory)
    try:
        file_size = await stream_upload_to_disk(
            upload_file=file,
            destination_path=upload_path,
            max_bytes=settings.max_file_size_bytes,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save upload: {str(e)}",
        )

    # Validate uploaded clip with ffprobe
    try:
        info = probe_media(str(upload_path))
        validate_clip(info, max_duration=settings.MAX_CLIP_DURATION_SECONDS)
    except FFmpegPipelineError as e:
        # Remove invalid uploaded file
        if upload_path.exists():
            try:
                upload_path.unlink()
            except OSError:
                pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Check disk space based on estimate
    estimated_output_bytes = estimate_output_size(
        input_file_size=file_size,
        input_duration=info.video_duration,
        target_duration=target_seconds,
    )
    if not check_disk_space_available(estimated_output_bytes):
        if upload_path.exists():
            try:
                upload_path.unlink()
            except OSError:
                pass
        _, _, free_bytes = get_disk_usage()
        est_mb = round(estimated_output_bytes / (1024 * 1024), 1)
        free_mb = round(free_bytes / (1024 * 1024), 1)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient disk space. Estimated output: {est_mb} MB, Available: {free_mb} MB.",
        )

    # Create Job record
    job = Job(
        id=job_id,
        filename=safe_name,
        input_path=str(upload_path),
        file_size_bytes=file_size,
        target_seconds=target_seconds,
        input_duration_seconds=info.video_duration,
        mode=mode_clean,
        status=JobStatus.QUEUED.value,
        progress=0.0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Enqueue in Celery
    try:
        task = process_video_job.delay(job.id)
        job.celery_task_id = task.id
        db.commit()
    except Exception as e:
        # If celery is not running in local test mode, log it
        pass

    return map_to_response(db, job)


@router.get("", response_model=JobListResponse)
def list_jobs(
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """List jobs with optional status filter, search, and pagination."""
    query = db.query(Job)

    if status_filter:
        query = query.filter(Job.status == status_filter.lower())

    if search:
        search_pattern = f"%{search}%"
        query = query.filter(Job.filename.ilike(search_pattern))

    total = query.count()
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    jobs = (
        query.order_by(Job.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    job_responses = [map_to_response(db, j) for j in jobs]

    return JobListResponse(
        jobs=job_responses,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Get single job status and details."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return map_to_response(db, job)


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Cancel a queued or processing job and terminate its process."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        return map_to_response(db, job)

    # If currently processing with a PID, kill the process group
    if job.process_pid:
        kill_process_group(job.process_pid)

    # Revoke in Celery
    if job.celery_task_id:
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception:
            pass

    job.status = JobStatus.CANCELLED.value
    job.process_pid = None
    db.commit()
    db.refresh(job)

    # Clean up partial output if any
    if job.output_path and os.path.exists(job.output_path):
        try:
            os.unlink(job.output_path)
        except OSError:
            pass

    return map_to_response(db, job)


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: str,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Retry a failed or cancelled job."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry a job with status '{job.status}'. Only failed or cancelled jobs can be retried."
        )

    # Ensure input file still exists
    if not job.input_path or not os.path.exists(job.input_path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Original input file is no longer available on disk."
        )

    job.status = JobStatus.QUEUED.value
    job.progress = 0.0
    job.eta_seconds = None
    job.error_message = None
    db.commit()
    db.refresh(job)

    # Enqueue task again
    try:
        task = process_video_job.delay(job.id)
        job.celery_task_id = task.id
        db.commit()
    except Exception:
        pass

    return map_to_response(db, job)


@router.delete("/{job_id}")
def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Delete a job and remove its files from disk."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # If running, terminate it
    if job.status == JobStatus.PROCESSING.value and job.process_pid:
        kill_process_group(job.process_pid)
    if job.celery_task_id:
        try:
            celery_app.control.revoke(job.celery_task_id, terminate=True)
        except Exception:
            pass

    # Remove files
    if job.input_path and os.path.exists(job.input_path):
        try:
            os.unlink(job.input_path)
        except OSError:
            pass

    if job.output_path and os.path.exists(job.output_path):
        try:
            os.unlink(job.output_path)
        except OSError:
            pass

    db.delete(job)
    db.commit()
    return {"message": "Job deleted successfully", "id": job_id}


@router.post("/batch-delete", response_model=BatchDeleteResponse)
def batch_delete_jobs(
    payload: BatchDeleteRequest,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Delete multiple jobs and their associated files."""
    deleted_ids = []
    failed_ids = []

    for jid in payload.job_ids:
        job = db.query(Job).filter(Job.id == jid).first()
        if not job:
            failed_ids.append(jid)
            continue

        if job.status == JobStatus.PROCESSING.value and job.process_pid:
            kill_process_group(job.process_pid)
        if job.celery_task_id:
            try:
                celery_app.control.revoke(job.celery_task_id, terminate=True)
            except Exception:
                pass

        if job.input_path and os.path.exists(job.input_path):
            try:
                os.unlink(job.input_path)
            except OSError:
                pass

        if job.output_path and os.path.exists(job.output_path):
            try:
                os.unlink(job.output_path)
            except OSError:
                pass

        db.delete(job)
        deleted_ids.append(jid)

    db.commit()
    return BatchDeleteResponse(deleted_ids=deleted_ids, failed_ids=failed_ids)


def _handle_media_stream(
    file_path_str: str,
    range_header: Optional[str],
    as_attachment: bool = False,
    download_filename: Optional[str] = None
) -> Response:
    """Helper to serve files with HTTP Range requests."""
    file_path = Path(file_path_str)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Requested file not found on disk")

    file_size = file_path.stat().st_size
    content_type = "video/mp4"

    disposition = "attachment" if as_attachment else "inline"
    safe_dl_name = sanitize_filename(download_filename or file_path.name)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disposition}; filename="{safe_dl_name}"',
    }

    if range_header:
        start, end = parse_range_header(range_header, file_size)
        content_length = (end - start) + 1
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(content_length)

        return StreamingResponse(
            range_file_iterator(file_path, start, end),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            headers=headers,
            media_type=content_type,
        )

    # Full content response
    headers["Content-Length"] = str(file_size)
    return StreamingResponse(
        range_file_iterator(file_path, 0, file_size - 1),
        status_code=status.HTTP_200_OK,
        headers=headers,
        media_type=content_type,
    )


@router.get("/{job_id}/stream")
def stream_job_video(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Stream completed job video for in-browser preview player with HTTP Range support."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED.value or not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not completed yet"
        )

    range_header = request.headers.get("Range")
    return _handle_media_stream(job.output_path, range_header, as_attachment=False)


@router.get("/{job_id}/download")
def download_job_video(
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Download completed job video with HTTP Range and attachment disposition."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status != JobStatus.COMPLETED.value or not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job is not completed yet"
        )

    out_name = f"loopforge_{Path(job.filename).stem}_{int(job.target_seconds)}s.mp4"
    range_header = request.headers.get("Range")
    return _handle_media_stream(
        job.output_path,
        range_header,
        as_attachment=True,
        download_filename=out_name,
    )
