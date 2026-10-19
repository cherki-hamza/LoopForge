import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from celery import shared_task
from app.core.celery_app import celery_app
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import Job, JobStatus
from app.services.ffmpeg_pipeline import (
    run_pipeline,
    FFmpegCancelledError,
    FFmpegPipelineError,
    kill_process_group,
)

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, name="app.tasks.process_job.process_video_job")
def process_video_job(self, job_id: str):
    """Celery task to run the FFmpeg looping pipeline for a job."""
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        # Check if already cancelled
        if job.status == JobStatus.CANCELLED.value:
            logger.info(f"Job {job_id} was cancelled before starting")
            return

        # Mark job as processing
        job.status = JobStatus.PROCESSING.value
        job.started_at = datetime.now(timezone.utc)
        job.celery_task_id = self.request.id
        job.progress = 0.0
        job.error_message = None
        db.commit()

        # Define callbacks for pipeline
        last_save_time = [0.0]

        def on_progress(pct: float, eta: float | None):
            import time
            now = time.time()
            # Throttle DB writes to at most once per 0.5s or on 100%
            if (now - last_save_time[0] >= 0.5) or pct >= 100.0:
                with SessionLocal() as inner_db:
                    inner_db.query(Job).filter(Job.id == job_id).update({
                        Job.progress: pct,
                        Job.eta_seconds: eta,
                    })
                    inner_db.commit()
                last_save_time[0] = now

        def on_pid(pid: int):
            with SessionLocal() as inner_db:
                inner_db.query(Job).filter(Job.id == job_id).update({
                    Job.process_pid: pid
                })
                inner_db.commit()

        def check_cancelled() -> bool:
            with SessionLocal() as inner_db:
                status_val = inner_db.query(Job.status).filter(Job.id == job_id).scalar()
                return status_val == JobStatus.CANCELLED.value

        output_filename = f"{job.id}_output.mp4"
        output_path = Path(settings.outputs_dir) / output_filename

        try:
            actual_mode, out_dur = run_pipeline(
                input_path=job.input_path,
                final_output_path=output_path,
                target_seconds=job.target_seconds,
                mode=job.mode,
                temp_dir_base=settings.temp_dir,
                on_progress=on_progress,
                on_pid=on_pid,
                timeout_seconds=settings.JOB_TIMEOUT_SECONDS,
                check_cancelled=check_cancelled,
            )

            # Mark completed
            with SessionLocal() as inner_db:
                inner_db.query(Job).filter(Job.id == job_id).update({
                    Job.status: JobStatus.COMPLETED.value,
                    Job.output_path: str(output_path),
                    Job.output_duration_seconds: out_dur,
                    Job.actual_mode_used: actual_mode,
                    Job.progress: 100.0,
                    Job.eta_seconds: 0.0,
                    Job.process_pid: None,
                    Job.completed_at: datetime.now(timezone.utc),
                })
                inner_db.commit()
            logger.info(f"Job {job_id} successfully completed. Output: {output_path}")

        except FFmpegCancelledError:
            logger.info(f"Job {job_id} was cancelled during execution")
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            with SessionLocal() as inner_db:
                inner_db.query(Job).filter(Job.id == job_id).update({
                    Job.status: JobStatus.CANCELLED.value,
                    Job.process_pid: None,
                })
                inner_db.commit()

        except Exception as e:
            logger.exception(f"Job {job_id} failed with error: {str(e)}")
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            with SessionLocal() as inner_db:
                inner_db.query(Job).filter(Job.id == job_id).update({
                    Job.status: JobStatus.FAILED.value,
                    Job.error_message: str(e),
                    Job.process_pid: None,
                })
                inner_db.commit()

    finally:
        db.close()
