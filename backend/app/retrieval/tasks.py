import math

from app.ingestion.providers import ProviderConfigurationError
from app.retrieval.runtime import ready_provider
from app.workers.celery_app import celery_app


@celery_app.task(
    name="app.retrieval.tasks.embed_query",
    expires=30,
    result_expires=60,
    resultrepr_maxsize=0,
)
def embed_query(query: str):
    # Expected failures are safe result codes, never exceptions containing input.
    try:
        provider = ready_provider()
        if len(provider.tokenize(query)) > provider.max_input_tokens:
            return {"error": "QUERY_TOO_LONG"}
        vector = provider.embed([query])[0]
        if len(vector) != provider.dimension or not all(
            math.isfinite(v) for v in vector
        ):
            return {"error": "EMBEDDING_INVALID"}
        return {"vector": vector, "dimension": provider.dimension}
    except ProviderConfigurationError as exc:
        return {
            "error": str(exc)
            if str(exc)
            in {"MODEL_WARMING", "MODEL_CACHE_UNAVAILABLE", "MODEL_INIT_FAILED"}
            else "EMBEDDING_UNAVAILABLE"
        }
    except Exception:  # noqa: BLE001 - worker boundary must not log provider/input details
        return {"error": "EMBEDDING_UNAVAILABLE"}
