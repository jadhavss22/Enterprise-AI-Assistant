"""Bounded readiness separate from core API liveness/dependency health."""

import asyncio
import json
import time

from fastapi import HTTPException
from redis.asyncio import Redis

from app.config import settings
from app.documents.validation import fail
from app.retrieval.pool import blocking_call
from app.retrieval.runtime import model_prefix

_vector_cache = None
_vector_cache_until = 0.0
_vector_lock = asyncio.Lock()


async def model_readiness(redis):
    rows = []
    async for key in redis.scan_iter(match=model_prefix() + "*", count=32):
        raw = await redis.get(key)
        try:
            row = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            continue
        if isinstance(row, dict) and row.get("state") in {"ready", "warming", "failed"}:
            rows.append(row)
        if len(rows) >= 128:
            break
    if any(row["state"] == "ready" for row in rows):
        return {"status": "ready", "code": None}
    if any(row["state"] == "warming" for row in rows):
        return {"status": "warming", "code": "MODEL_WARMING"}
    if rows:
        known = {
            "MODEL_CACHE_UNAVAILABLE",
            "INDEX_PROVIDER_UNCONFIGURED",
            "LOCAL_EMBEDDING_DIMENSION_MISMATCH",
            "EMBEDDING_INVALID",
            "UNKNOWN_EMBEDDING_PROVIDER",
            "MODEL_INIT_FAILED",
        }
        code = rows[0].get("error")
        return {
            "status": "failed",
            "code": code if code in known else "MODEL_INIT_FAILED",
        }
    return {"status": "unavailable", "code": "WORKER_UNAVAILABLE"}


async def require_model_ready():
    try:
        async with Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        ) as redis:
            async with asyncio.timeout(2):
                state = await model_readiness(redis)
    except Exception:  # noqa: BLE001 - safe readiness error, no Redis URL
        raise fail(
            503, "READINESS_UNAVAILABLE", "Search readiness is temporarily unavailable"
        ) from None
    if state["status"] != "ready":
        messages = {
            "MODEL_WARMING": "The search model is warming up; check availability shortly",
            "WORKER_UNAVAILABLE": "The search worker is unavailable; start the worker and check availability",
            "MODEL_CACHE_UNAVAILABLE": "The local model cache is missing or invalid; an administrator must provision it",
        }
        exc = fail(
            503,
            state["code"],
            messages.get(
                state["code"],
                "Search model initialization failed; contact your administrator",
            ),
        )
        if state["status"] in {"warming", "unavailable"}:
            exc.headers = {"Retry-After": "3"}
        raise exc


async def vector_readiness():
    global _vector_cache, _vector_cache_until
    from app.retrieval.providers import retrieval_index

    if _vector_cache is not None and time.monotonic() < _vector_cache_until:
        return _vector_cache
    try:
        async with asyncio.timeout(settings.retrieval_readiness_timeout_seconds):
            async with _vector_lock:
                if _vector_cache is not None and time.monotonic() < _vector_cache_until:
                    return _vector_cache
                index = await blocking_call(retrieval_index)
                stats = await blocking_call(index.describe_index_stats, timeout=2)
                dimension = (
                    stats.get("dimension")
                    if isinstance(stats, dict)
                    else stats.dimension
                )
                expected = (
                    settings.local_embedding_dimensions
                    if settings.embedding_provider == "local"
                    else settings.azure_openai_embedding_dimensions
                )
                state = (
                    {"status": "ready", "code": None}
                    if dimension == expected
                    else {"status": "failed", "code": "EMBEDDING_INVALID"}
                )
    except HTTPException as exc:
        code = (
            exc.detail.get("code")
            if isinstance(exc.detail, dict)
            else "VECTOR_UNAVAILABLE"
        )
        state = {
            "status": "failed" if code == "SEARCH_UNCONFIGURED" else "unavailable",
            "code": code,
        }
    except Exception:  # noqa: BLE001 - provider state never includes secrets/network internals
        state = {"status": "unavailable", "code": "VECTOR_UNAVAILABLE"}
    _vector_cache, _vector_cache_until = state, time.monotonic() + 5
    return state


async def retrieval_readiness(redis):
    try:
        async with asyncio.timeout(2):
            model = await model_readiness(redis)
    except Exception:  # noqa: BLE001 - status endpoint remains bounded and privacy safe
        model = {"status": "unavailable", "code": "READINESS_UNAVAILABLE"}
    vector = (
        await vector_readiness()
        if model["status"] == "ready"
        else {"status": "not_checked", "code": None}
    )
    status = model["status"] if model["status"] != "ready" else vector["status"]
    return {
        "status": status,
        "model": model["status"],
        "vectors": vector["status"],
        "code": model["code"] or vector["code"],
        "retry_after": 5 if status != "ready" else 0,
    }
