from celery import Celery

from app.config import settings

celery_app = Celery(
    "enterprise_ai_assistant",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.ingestion.tasks"],
)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_acks_late = True
celery_app.conf.task_reject_on_worker_lost = True
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.worker_concurrency = 1
celery_app.conf.beat_schedule = {
    "reconcile-ingestion-jobs-every-five-minutes": {
        "task": "app.ingestion.tasks.reconcile_pending_ingestion_jobs",
        "schedule": 300.0,
    }
}


@celery_app.task(name="app.workers.celery_app.scaffold_ping")
def scaffold_ping() -> str:
    return "worker-ready"


@celery_app.task(name="app.workers.celery_app.embedding_runtime_probe")
def embedding_runtime_probe() -> dict[str, int | str]:
    """Exercise the configured embedding provider inside a worker process."""
    from app.ingestion.providers import embedding_provider

    provider = embedding_provider()
    vector = provider.embed(["local worker embedding smoke check"])[0]
    return {
        "provider": settings.embedding_provider,
        "dimension": len(vector),
        "max_input_tokens": provider.max_input_tokens,
        "batch_size": provider.batch_size,
    }
