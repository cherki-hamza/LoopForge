from celery import Celery
from celery.schedules import crontab
from app.core.config import settings

celery_app = Celery(
    "loopforge",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.process_job",
        "app.tasks.retention",
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # Strict FIFO queueing
    worker_concurrency=settings.MAX_CONCURRENT_JOBS,
    beat_schedule={
        "cleanup-retention-hourly": {
            "task": "app.tasks.retention.cleanup_retention",
            "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
        },
    },
)
