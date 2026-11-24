"""Live paired synthetic benchmark with disposable Mongo/Pinecone state.

Run explicitly with --allow-synthetic-writes. Never touches existing documents.
The output is also a cleanup journal containing only disposable resource IDs.
"""

import argparse
import asyncio
import hashlib
import json
import secrets
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from bson import ObjectId
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import get_db, get_redis
from app.config import settings
from app.main import app
from app.retrieval import service
from app.retrieval.providers import retrieval_index
from app.retrieval.tasks import embed_query
from app.workers.celery_app import embedding_runtime_probe
from scripts.hybrid_evaluation import (
    EvaluationSession,
    dataset_sha256,
    report,
    validate_dataset,
    validate_output,
)
from scripts.hybrid_retrieval_benchmark import observe


def save(path, result):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as journal:
            temporary = Path(journal.name)
            journal.write(json.dumps(result, indent=2) + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def provision_vector(index, **kwargs):
    # Cancelling to_thread cannot stop an accepted SDK write. Drain it before
    # namespace cleanup so a late upsert cannot recreate an already deleted one.
    write = asyncio.create_task(asyncio.to_thread(index.upsert, **kwargs))
    try:
        return await asyncio.shield(write)
    except asyncio.CancelledError:
        while not write.done():
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(write)
        with suppress(Exception):
            write.result()
        raise


async def worker_result(task, *args):
    result = await asyncio.to_thread(
        task.apply_async,
        args=list(args),
        argsrepr="[<synthetic evaluation input>]",
        retry=False,
    )
    try:
        return await asyncio.to_thread(result.get, timeout=240)
    finally:
        await asyncio.to_thread(result.forget)


async def compare(dataset, output, rounds):
    lock_path = output.with_suffix(output.suffix + ".lock")
    lease = lock_path.open("x", encoding="utf-8")
    try:
        return await _compare(dataset, output, rounds)
    finally:
        lease.close()
        lock_path.unlink()


async def _compare(dataset, output, rounds):
    validate_output(dataset, output)
    source = json.loads(dataset.read_text(encoding="utf-8"))
    validate_dataset(source)
    # One bounded chunk per synthetic source. This evaluates retrieval, not ingestion.
    if any(len(doc["text"]) > 1000 for doc in source["documents"]):
        raise ValueError("Synthetic fixture exceeds the single-chunk bound")
    mongo = AsyncIOMotorClient(
        settings.mongodb_url, tz_aware=True, serverSelectionTimeoutMS=5000
    )
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    run_id = "test_hybrid_expanded_" + uuid4().hex
    db = mongo[run_id]
    docs = [{**doc, "_id": ObjectId()} for doc in source["documents"]]
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "dataset": dataset.name,
        "dataset_sha256": dataset_sha256(dataset),
        "dataset_hash_format": "utf8-lf",
        "mode": "live_worker_and_pinecone",
        "rounds": rounds,
        "settings": {
            "semantic_cutoff": settings.retrieval_min_score,
            "result_limit": settings.retrieval_result_limit,
            "candidate_limit": settings.retrieval_candidate_limit,
            "pinecone_index_name": settings.pinecone_index_name,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.local_embedding_model
            if settings.embedding_provider == "local"
            else "configured_azure_deployment",
            "search_timeout_seconds": settings.retrieval_search_timeout_seconds,
            "namespace_timeout_seconds": settings.retrieval_namespace_timeout_seconds,
            "reranking": False,
            "rewriting": False,
        },
        "resources": {
            "database": run_id,
            "namespaces": [f"{doc['_id']}-v1" for doc in docs],
        },
        "cleanup": "pending",
        "complete": False,
        "cases": [],
    }
    save(output, result)
    index = None
    previous = app.dependency_overrides.copy()
    previous_prefix = settings.auth_rate_prefix
    settings.auth_rate_prefix = run_id + ":"
    try:
        index = await asyncio.to_thread(retrieval_index)
        print("Warming the configured embedding worker", flush=True)
        probe = await worker_result(embedding_runtime_probe)
        if probe.get("dimension") is None:
            raise RuntimeError("Embedding worker probe failed")
        result["worker"] = probe
        password = secrets.token_urlsafe(32)
        await bootstrap_in_database(
            db,
            "synthetic-eval@example.com",
            "Synthetic Evaluation",
            "engineering",
            password,
        )
        user = await db.users.find_one(
            {"email_normalized": "synthetic-eval@example.com"}
        )
        for doc in docs:
            payload = await worker_result(embed_query, doc["text"])
            if "vector" not in payload:
                raise RuntimeError("Synthetic embedding failed")
            vector_id = hashlib.sha256(f"{doc['_id']}:1:0".encode()).hexdigest()
            metadata = {
                "document_id": str(doc["_id"]),
                "index_version": 1,
                "chunk_index": 0,
                "text": doc.pop("text"),
            }
            await provision_vector(
                index,
                vectors=[(vector_id, payload["vector"], metadata)],
                namespace=f"{doc['_id']}-v1",
                timeout=20,
            )
            doc.update(
                uploaded_by=user["_id"],
                department_id=user["department_id"],
                visibility="PRIVATE",
                allowed_user_ids=[],
                status="READY",
                active_index_version=1,
                chunk_count=1,
            )
            print(f"Provisioned synthetic source {doc['filename']}", flush=True)
        await db.documents.insert_many(docs)
        # Explicit bounded visibility barrier before timing; no query replay/cache.
        for doc in docs:
            vector_id = hashlib.sha256(f"{doc['_id']}:1:0".encode()).hexdigest()
            for attempt in range(20):
                fetched = await asyncio.to_thread(
                    index.fetch,
                    ids=[vector_id],
                    namespace=f"{doc['_id']}-v1",
                    timeout=10,
                )
                if vector_id in fetched.vectors:
                    break
                await asyncio.sleep(1)
            else:
                raise RuntimeError("Synthetic indexing visibility deadline")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_redis] = lambda: redis
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                headers={"Origin": settings.frontend_origin},
                json={"email": "synthetic-eval@example.com", "password": password},
            )
            if login.status_code != 200:
                raise RuntimeError("Synthetic login failed")
            session = EvaluationSession(
                client,
                login.json()["access_token"],
                settings.frontend_origin,
                settings.jwt_access_ttl_seconds,
            )
            result["warmups"] = []
            for hybrid in (False, True):
                warmup = await observe(
                    client, await session.access_token(), source["cases"][0], hybrid
                )
                result["warmups"].append({"hybrid": hybrid, **warmup})
                if warmup["http"] != 200:
                    raise RuntimeError("Live search warmup failed")
            original_search = service.search_namespaces
            for repeat in range(rounds):
                for ordinal, case in enumerate(source["cases"]):
                    pair = dict(case, round=repeat + 1)
                    order = (True, False) if (ordinal + repeat) % 2 else (False, True)
                    pair["first_mode"] = "hybrid" if order[0] else "semantic"
                    for hybrid in order:
                        errors = []

                        async def search(*args, errors=errors):
                            try:
                                return await original_search(*args)
                            except Exception as exc:
                                errors.append(type(exc).__name__)
                                raise

                        with patch.object(service, "search_namespaces", search):
                            token = await session.access_token()
                            observation = await observe(client, token, case, hybrid)
                        observation["semantic_errors"] = errors
                        pair["hybrid" if hybrid else "semantic"] = observation
                    result["cases"].append(pair)
                    result["session_refreshes"] = session.refreshes
                    save(output, result)
                    print(
                        f"{len(result['cases'])}/{rounds * len(source['cases'])} {case['id']}: semantic={pair['semantic']['http']} hybrid={pair['hybrid']['http']}",
                        flush=True,
                    )
        result.update(report(result["cases"]))
        result["complete"] = True
    except Exception as exc:  # noqa: BLE001 - persist safe class only, never provider bodies
        result["error_type"] = type(exc).__name__
        result["decision"] = "keep_disabled_incomplete_evaluation"
    finally:
        app.dependency_overrides = previous
        settings.auth_rate_prefix = previous_prefix
        failures = []
        if index is not None:
            for namespace in result["resources"]["namespaces"]:
                try:
                    await asyncio.to_thread(
                        index.delete, delete_all=True, namespace=namespace, timeout=20
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup every namespace
                    failures.append(
                        {"namespace": namespace, "error_type": type(exc).__name__}
                    )
        try:
            await mongo.drop_database(run_id)
            keys = [key async for key in redis.scan_iter(match=run_id + ":*")]
            if keys:
                await redis.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            failures.append({"state": "local", "error_type": type(exc).__name__})
        await redis.aclose()
        mongo.close()
        result["cleanup"] = "failed" if failures else "completed"
        result["cleanup_errors"] = failures
        if failures:
            result["decision"] = "keep_disabled_cleanup_required"
        result["finished_at"] = datetime.now(UTC).isoformat()
        save(output, result)
    if not result["complete"] or result["cleanup"] != "completed":
        raise SystemExit("Evaluation incomplete; inspect the output journal.")
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--allow-synthetic-writes", action="store_true", required=True)
    args = parser.parse_args()
    with (
        patch.object(settings, "retrieval_rate_limit", 10000),
        patch.object(settings, "retrieval_rerank_enabled", False),
        patch.object(settings, "retrieval_query_rewrite_enabled", False),
    ):
        asyncio.run(compare(args.dataset, args.output, args.rounds))
