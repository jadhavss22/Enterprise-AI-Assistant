"""Regression coverage for review R1-R5; external model/vector calls are doubles."""

import asyncio
import hashlib
import json
import sys
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException
from test_admin_users import bearer, env  # noqa: F401
from test_document_review_fixes import FakeBlobService, FakeProvider, worker_db  # noqa: F401
from test_ingestion_embeddings import FakeModel

from app.config import settings
from app.ingestion import providers as embedding
from app.ingestion import tasks as ingestion
from app.retrieval import pool, providers, readiness, runtime, service, tasks


@pytest.mark.asyncio
async def test_timed_out_calls_keep_capacity_until_real_completion():
    bounded = pool.ProviderPool(2)
    release = threading.Event()
    lock = threading.Lock()
    running = 0
    peak = 0

    def operation():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            release.wait(3)
        finally:
            with lock:
                running -= 1
        return "done"

    try:
        for _ in range(2):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(bounded.call(operation), 0.03)
        for _ in range(5):
            with pytest.raises(HTTPException) as error:
                await bounded.call(operation)
            assert error.value.detail["code"] == "PROVIDER_BUSY"
        assert running == peak == 2
        release.set()
        for _ in range(100):
            if running == 0:
                break
            await asyncio.sleep(0.01)
        assert await bounded.call(lambda: "recovered") == "recovered"
        assert peak == 2
    finally:
        release.set()
        bounded.close()


@pytest.mark.asyncio
async def test_sibling_failure_does_not_free_running_provider_calls(monkeypatch):
    bounded = pool.ProviderPool(2)
    monkeypatch.setattr(pool, "provider_pool", bounded)
    release, started = threading.Event(), threading.Event()
    docs = [{"_id": ObjectId(), "active_index_version": 1} for _ in range(2)]

    def query(index, doc, vector):
        if doc["_id"] == docs[0]["_id"]:
            started.set()
            release.wait(3)
            return {"matches": []}
        assert started.wait(1)
        raise RuntimeError("synthetic provider failure")

    monkeypatch.setattr(providers, "retrieval_index", lambda: object())
    monkeypatch.setattr(providers, "_query_namespace", query)
    try:
        with pytest.raises(RuntimeError):
            await providers.search_namespaces(docs, [0.1])
        # One failed call frees its own slot; the still-running call retains its slot.
        extra = asyncio.create_task(bounded.call(lambda: release.wait(3)))
        await asyncio.sleep(0.03)
        with pytest.raises(HTTPException):
            await bounded.call(lambda: None)
        release.set()
        await extra
    finally:
        release.set()
        bounded.close()


def test_reprocessing_failure_preserves_active_count_and_passages(
    worker_db, monkeypatch  # noqa: F811
):
    client, db = worker_db
    now = datetime.now(UTC)
    doc = {
        "_id": ObjectId(),
        "filename": "synthetic.txt",
        "uploaded_by": ObjectId(),
        "blob_key": "synthetic",
        "status": "READY",
        "active_index_version": 1,
        "chunk_count": 3,
    }
    job = {
        "_id": ObjectId(),
        "document_id": doc["_id"],
        "target_index_version": 2,
        "status": "QUEUED",
        "dispatch_status": "PENDING",
        "attempt_count": 0,
        "max_attempts": 1,
        "fencing_token": 0,
        "created_at": now,
        "updated_at": now,
    }
    db.documents.insert_one(doc)
    db.ingestion_jobs.insert_one(job)
    monkeypatch.setattr(ingestion, "BlobServiceClient", FakeBlobService)
    monkeypatch.setattr(ingestion, "embedding_provider", FakeProvider)

    def fail(*args):
        current = db.documents.find_one({"_id": doc["_id"]})
        assert current["active_index_version"] == 1 and current["chunk_count"] == 3
        assert (
            db.ingestion_jobs.find_one({"_id": job["_id"]})["pending_chunk_count"] == 1
        )
        raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")

    monkeypatch.setattr(ingestion, "_embed_chunks", fail)
    ingestion.process_ingestion_job.run(str(job["_id"]))
    current = db.documents.find_one({"_id": doc["_id"]})
    assert current["active_index_version"] == 1 and current["chunk_count"] == 3
    assert db.ingestion_jobs.find_one({"_id": job["_id"]})["status"] == "FAILED"
    match = {
        "id": hashlib.sha256(f"{doc['_id']}:1:2".encode()).hexdigest(),
        "score": 0.9,
        "metadata": {
            "document_id": str(doc["_id"]),
            "index_version": 1,
            "chunk_index": 2,
            "text": "old active evidence",
        },
    }
    assert service.checked_candidate(current, match)
    # Successful activation publishes the new version/count atomically.
    db.ingestion_jobs.update_one(
        {"_id": job["_id"]}, {"$set": {"status": "PROCESSING"}}
    )
    current_job = db.ingestion_jobs.find_one({"_id": job["_id"]})
    assert (
        ingestion._activate(db, client, current_job, current, f"{doc['_id']}-v2", 1)
        == 1
    )
    current = db.documents.find_one({"_id": doc["_id"]})
    assert (current["active_index_version"], current["chunk_count"]) == (2, 1)
    assert service.checked_candidate(current, match) is None


def test_cache_only_loading_and_missing_cache_are_explicit(monkeypatch):
    monkeypatch.setattr(settings, "local_embedding_cache_only", True)
    monkeypatch.setattr(embedding, "_local_model", None)
    calls = []

    def factory(*args, **kwargs):
        calls.append(kwargs)
        return FakeModel()

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=factory),
    )
    assert embedding._load_local_model() is embedding._load_local_model()
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
    monkeypatch.setattr(embedding, "_local_model", None)

    def missing(*args, **kwargs):
        raise OSError("private provider/cache details")

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=missing),
    )
    with pytest.raises(
        embedding.ProviderConfigurationError, match="^MODEL_CACHE_UNAVAILABLE$"
    ):
        embedding._load_local_model()
    assert embedding._local_model is None


def test_queries_never_initialize_a_cold_model(monkeypatch):
    monkeypatch.setattr(runtime, "_state", "warming")
    monkeypatch.setattr(runtime, "_error", None)
    monkeypatch.setattr(runtime, "_provider", None)
    assert tasks.embed_query.run("synthetic question") == {"error": "MODEL_WARMING"}

    class Provider(FakeProvider):
        pass

    provider = Provider()
    monkeypatch.setattr(runtime, "embedding_provider", lambda: provider)
    runtime.warm_model()
    assert runtime.runtime_state()["state"] == "ready"
    assert runtime.ready_provider() is provider
    assert tasks.embed_query.run("synthetic question")["dimension"] == 3


def test_warmup_failure_is_sanitized_and_not_ready(monkeypatch):
    monkeypatch.setattr(runtime, "_state", "warming")
    monkeypatch.setattr(runtime, "_error", None)
    monkeypatch.setattr(runtime, "_provider", None)

    def fail():
        raise RuntimeError("private provider details")

    monkeypatch.setattr(runtime, "embedding_provider", fail)
    runtime.warm_model()
    assert runtime.runtime_state() == {"state": "failed", "error": "MODEL_INIT_FAILED"}
    assert tasks.embed_query.run("synthetic question") == {"error": "MODEL_INIT_FAILED"}


@pytest.mark.asyncio
async def test_readiness_states_do_not_initialize_model(env, monkeypatch):  # noqa: F811
    redis, client = env["redis"], env["client"]
    prefix = settings.auth_rate_prefix + "model:"
    monkeypatch.setattr(readiness, "model_prefix", lambda: prefix)
    vectors = []

    async def vector_ready():
        vectors.append(True)
        return {"status": "ready", "code": None}

    monkeypatch.setattr(readiness, "vector_readiness", vector_ready)
    headers = bearer(env["admin"])
    assert (await client.get("/api/v1/retrieval/readiness")).status_code == 401

    async def read():
        response = await client.get("/api/v1/retrieval/readiness", headers=headers)
        assert response.status_code == 200
        return response.json()

    assert (await read())["code"] == "WORKER_UNAVAILABLE"
    await redis.set(prefix + "child", json.dumps({"state": "warming"}), ex=15)
    assert (await read())["status"] == "warming"
    assert vectors == []
    await redis.set(
        prefix + "child",
        json.dumps({"state": "failed", "error": "MODEL_CACHE_UNAVAILABLE"}),
        ex=15,
    )
    assert (await read())["code"] == "MODEL_CACHE_UNAVAILABLE"
    await redis.set(prefix + "child", json.dumps({"state": "ready"}), ex=15)
    result = await read()
    assert result["status"] == "ready" and result["request_id"]
    assert len(vectors) == 1
    await redis.delete(prefix + "child")
    assert (await read())["status"] == "unavailable"


@pytest.mark.asyncio
async def test_vector_readiness_is_bounded_cached_and_recovers(monkeypatch):
    monkeypatch.setattr(readiness, "_vector_cache", None)
    monkeypatch.setattr(readiness, "_vector_cache_until", 0)
    calls = []

    class Index:
        def describe_index_stats(self, **kwargs):
            calls.append(True)
            raise RuntimeError("private network details")

    monkeypatch.setattr(providers, "retrieval_index", Index)
    assert (await readiness.vector_readiness())["code"] == "VECTOR_UNAVAILABLE"
    assert (await readiness.vector_readiness())["code"] == "VECTOR_UNAVAILABLE"
    assert len(calls) == 1
    monkeypatch.setattr(readiness, "_vector_cache_until", 0)
    monkeypatch.setattr(
        Index, "describe_index_stats", lambda *a, **k: {"dimension": 384}
    )
    assert (await readiness.vector_readiness())["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_deadline_keeps_provider_work_bounded(monkeypatch):
    monkeypatch.setattr(readiness, "_vector_cache", None)
    monkeypatch.setattr(readiness, "_vector_cache_until", 0)
    monkeypatch.setattr(settings, "retrieval_readiness_timeout_seconds", 0.03)
    bounded = pool.ProviderPool(1)
    monkeypatch.setattr(pool, "provider_pool", bounded)
    release = threading.Event()

    def delayed_index():
        release.wait(2)
        return object()

    monkeypatch.setattr(providers, "retrieval_index", delayed_index)
    try:
        state = await readiness.vector_readiness()
        assert state["code"] == "VECTOR_UNAVAILABLE"
        with pytest.raises(HTTPException):
            await bounded.call(lambda: None)
    finally:
        release.set()
        bounded.close()
