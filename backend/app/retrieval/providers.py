import asyncio
import logging
import math
import time

from celery.exceptions import TimeoutError as CeleryTimeout
from pinecone import Pinecone

from app.config import settings
from app.documents.validation import fail
from app.retrieval.pool import blocking_call
from app.retrieval.readiness import require_model_ready
from app.retrieval.tasks import embed_query

logger = logging.getLogger(__name__)


async def query_embedding(query):
    await require_model_ready()
    result = None
    completed = False
    try:
        result = await blocking_call(
            embed_query.apply_async,
            args=[query],
            argsrepr="[<redacted query>]",
            expires=settings.retrieval_embedding_timeout_seconds,
            retry=False,
        )
        payload = await blocking_call(
            result.get,
            timeout=settings.retrieval_embedding_timeout_seconds,
            propagate=False,
        )
        completed = True
        if isinstance(payload, dict) and payload.get("error") == "QUERY_TOO_LONG":
            raise fail(
                422,
                "QUERY_TOO_LONG",
                "Shorten the question to fit the embedding model input limit",
            )
        if isinstance(payload, dict) and payload.get("error") in {
            "MODEL_WARMING",
            "MODEL_CACHE_UNAVAILABLE",
            "MODEL_INIT_FAILED",
        }:
            code = payload["error"]
            exc = fail(
                503,
                code,
                "The search model is warming up; check availability shortly"
                if code == "MODEL_WARMING"
                else "Search model initialization failed; contact your administrator",
            )
            if code == "MODEL_WARMING":
                exc.headers = {"Retry-After": "3"}
            raise exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise fail(
                503,
                "EMBEDDING_UNAVAILABLE",
                "Search embeddings are temporarily unavailable",
            )
        vector = payload.get("vector")
        expected = (
            settings.local_embedding_dimensions
            if settings.embedding_provider == "local"
            else settings.azure_openai_embedding_dimensions
        )
        if (
            not isinstance(vector, list)
            or len(vector) != expected
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector)
        ):
            raise fail(
                503,
                "EMBEDDING_INVALID",
                "Search embedding configuration is incompatible",
            )
        return vector
    except CeleryTimeout:
        await require_model_ready()
        raise fail(
            503,
            "SEARCH_BUSY",
            "The embedding worker is busy; retry shortly",
        ) from None
    except (OSError, ConnectionError):
        raise fail(
            503,
            "EMBEDDING_UNAVAILABLE",
            "Search embeddings are temporarily unavailable",
        ) from None
    finally:
        if result is not None:
            # A running task may finish later; the short result TTL bounds retention.
            try:
                if not completed:
                    await blocking_call(result.revoke, terminate=False)
                await blocking_call(result.forget)
            except Exception:  # noqa: BLE001 - best-effort ephemeral result cleanup
                logger.warning("retrieval.result_cleanup_failed")


def _query_namespace(index, doc, vector):
    version = doc["active_index_version"]
    return index.query(
        namespace=f"{doc['_id']}-v{version}",
        vector=vector,
        top_k=settings.retrieval_candidate_limit,
        filter={
            "document_id": {"$eq": str(doc["_id"])},
            "index_version": {"$eq": version},
        },
        include_metadata=True,
        include_values=False,
        timeout=settings.retrieval_namespace_timeout_seconds,
    )


def retrieval_index():
    if not settings.pinecone_api_key or not settings.pinecone_index_name:
        raise fail(503, "SEARCH_UNCONFIGURED", "Document search is not configured")
    return Pinecone(
        api_key=settings.pinecone_api_key,
        timeout=settings.retrieval_namespace_timeout_seconds,
    ).index(settings.pinecone_index_name)


async def search_namespaces(documents, vector):
    async with asyncio.timeout(settings.retrieval_search_timeout_seconds):
        return await _search_namespaces(documents, vector)


async def _search_namespaces(documents, vector):
    # Each admitted search has a bounded fan-out; service admission bounds total work.
    index = await blocking_call(retrieval_index)
    budget = asyncio.Semaphore(settings.retrieval_search_concurrency)

    async def one(doc):
        async with budget:
            response = await blocking_call(_query_namespace, index, doc, vector)
            matches = (
                response.get("matches", [])
                if isinstance(response, dict)
                else response.matches
            )
            return [(doc, match) for match in matches]

    tasks = [asyncio.create_task(one(doc)) for doc in documents]
    started = time.monotonic()
    try:
        batches = await asyncio.wait_for(
            asyncio.gather(*tasks), settings.retrieval_search_timeout_seconds
        )
        return [item for batch in batches for item in batch], round(
            (time.monotonic() - started) * 1000
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
