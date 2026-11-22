"""Celery app for background jobs (data syncs, reports, alerts). Tasks are added in later milestones."""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("lumen", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
)
app = celery_app  # `celery -A app.worker` looks for `app`


@celery_app.task(name="lumen.ping")
def ping() -> str:
    return "pong"
