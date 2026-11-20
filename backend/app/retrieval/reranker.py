"""Pinned, cache-only cross-encoder in the Celery execution process."""

import logging
import math
import threading

from celery.signals import worker_process_init

from app.config import settings

MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "ce0834f22110de6d9222af7a7a03628121708969"
MAX_PAIR_CHARACTERS = 100000
logger = logging.getLogger(__name__)
_model = None


def load_model(*, local_files_only=True):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(
        MODEL,
        revision=REVISION,
        device="cpu",
        max_length=384,
        local_files_only=local_files_only,
        trust_remote_code=False,
        model_kwargs={"use_safetensors": True},
    )


def warm_model():
    global _model
    try:
        model = load_model()
        scores = predict(model, [["initialization", "initialization"]])
        if len(scores) != 1 or not math.isfinite(float(scores[0])):
            raise ValueError
        _model = model
        logger.info("retrieval.reranker_ready")
    except Exception as exc:  # noqa: BLE001 - log neither SDK bodies nor source text
        _model = None
        logger.warning("retrieval.reranker_unavailable type=%s", type(exc).__name__)


def predict(model, pairs):
    from torch import nn

    return model.predict(
        pairs,
        batch_size=8,
        activation_fn=nn.Identity(),
        show_progress_bar=False,
    )


def score_pairs(pairs):
    if not settings.retrieval_rerank_enabled or _model is None:
        return {"error": "RERANK_UNAVAILABLE"}
    if (
        not isinstance(pairs, list)
        or not 1 <= len(pairs) <= settings.retrieval_candidate_limit
        or any(
            not isinstance(pair, list)
            or len(pair) != 2
            or any(not isinstance(text, str) or not text.strip() for text in pair)
            for pair in pairs
        )
    ):
        return {"error": "RERANK_INPUT_INVALID"}
    if sum(len(text) for pair in pairs for text in pair) > MAX_PAIR_CHARACTERS:
        return {"error": "RERANK_INPUT_INVALID"}
    try:
        scores = [float(score) for score in predict(_model, pairs)]
        if len(scores) != len(pairs) or not all(
            math.isfinite(score) for score in scores
        ):
            return {"error": "RERANK_INVALID"}
        return {"scores": scores}
    except Exception:  # noqa: BLE001 - task results contain fixed codes only
        return {"error": "RERANK_UNAVAILABLE"}


@worker_process_init.connect(weak=False)
def start_reranker(**kwargs):
    global _model
    _model = None
    if settings.retrieval_rerank_enabled:
        threading.Thread(target=warm_model, name="reranker-warmup", daemon=True).start()
