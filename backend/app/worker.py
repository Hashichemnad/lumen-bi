"""Celery app for background jobs (data syncs now; reports and alerts later)."""

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
    beat_schedule={
        # Each dataset has its own cron schedule; this checks every minute which ones are due
        "dispatch-due-syncs": {"task": "lumen.dispatch_due_syncs", "schedule": 60.0},
    },
)
app = celery_app  # `celery -A app.worker` looks for `app`


@celery_app.task(name="lumen.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="lumen.sync_dataset")
def sync_dataset(run_id: int) -> None:
    from app.services.sync import execute

    execute(run_id)


@celery_app.task(name="lumen.dispatch_due_syncs")
def dispatch_due_syncs() -> int:
    from app.services.sync import dispatch_due

    return dispatch_due()


@celery_app.task(name="lumen.render_dashboard")
def render_dashboard(user_id: int, dashboard_id: int, fmt: str, state: str | None = None) -> str:
    from app.services.render import render_b64

    return render_b64(user_id, dashboard_id, fmt, state)
