from celery import Celery

from app.config import settings

celery_app = Celery(
    "enterprise_ai_assistant",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.task_default_queue = "default"


@celery_app.task(name="app.workers.celery_app.scaffold_ping")
def scaffold_ping() -> str:
    return "worker-ready"

