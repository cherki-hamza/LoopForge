import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from celery import shared_task
from app.core.celery_app import celery_app
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.models import Job

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.retention.cleanup_retention")
def cleanup_retention():
    """Delete inputs and outputs older than RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.RETENTION_DAYS)
    logger.info(f"Running retention cleanup for jobs older than {cutoff.isoformat()}")

    db = SessionLocal()
    deleted_files_count = 0
    try:
        old_jobs = db.query(Job).filter(Job.created_at < cutoff).all()
        for job in old_jobs:
            # Delete input file
            if job.input_path and os.path.exists(job.input_path):
                try:
                    os.unlink(job.input_path)
                    deleted_files_count += 1
                except OSError as e:
                    logger.warning(f"Could not remove input file {job.input_path}: {e}")

            # Delete output file
            if job.output_path and os.path.exists(job.output_path):
                try:
                    os.unlink(job.output_path)
                    deleted_files_count += 1
                except OSError as e:
                    logger.warning(f"Could not remove output file {job.output_path}: {e}")

            # Delete job record from database
            db.delete(job)

        db.commit()
        logger.info(f"Retention cleanup finished: cleaned {len(old_jobs)} jobs and {deleted_files_count} files.")
    except Exception as e:
        logger.exception(f"Error during retention cleanup: {e}")
        db.rollback()
    finally:
        db.close()
