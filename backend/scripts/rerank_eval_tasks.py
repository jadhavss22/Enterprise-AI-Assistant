"""Opt-in synthetic diagnostic task; worker loads this module explicitly."""

import time

from app.retrieval.reranker import score_pairs
from app.retrieval.tasks import embed_query
from app.workers.celery_app import celery_app


@celery_app.task(
    name="evaluation.rerank_job", expires=60, result_expires=60, resultrepr_maxsize=0
)
def measured_job(kind, payload, submitted_ms):
    started = time.time() * 1000
    if kind == "embedding":
        result = embed_query.run(payload)
        valid = isinstance(result, dict) and "vector" in result
    elif kind == "rerank":
        result = score_pairs(payload)
        valid = isinstance(result, dict) and "scores" in result
    else:
        valid = False
    finished = time.time() * 1000
    return {
        "kind": kind,
        "valid": valid,
        "queue_wait_ms": max(0, started - submitted_ms),
        "execution_ms": finished - started,
        "end_to_end_ms": finished - submitted_ms,
    }
