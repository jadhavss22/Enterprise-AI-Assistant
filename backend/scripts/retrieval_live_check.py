"""Opt-in live worker/Pinecone evaluation using only named synthetic fixtures.

Run inside API container: python -m scripts.retrieval_live_check --dataset /path/to/dataset.json
Creates disposable auth/Mongo state, reads existing synthetic vectors, never upserts.
"""

import argparse
import asyncio
import json
import secrets
from pathlib import Path
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import get_db, get_redis
from app.config import settings
from app.main import app
from app.workers.celery_app import embedding_runtime_probe


async def main(dataset):
    source = json.loads(Path(dataset).read_text())
    allowed_names = {
        "ingestion-smoke-test.txt",
        "qa-support-policy.pdf",
        "qa-training-policy.docx",
    }
    mongo = AsyncIOMotorClient(settings.mongodb_url, tz_aware=True)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    name = "test_live_retrieval_" + uuid4().hex
    old_prefix = settings.auth_rate_prefix
    previous = app.dependency_overrides.copy()
    settings.auth_rate_prefix = name + ":"
    db = mongo[name]
    try:
        docs = (
            await mongo[settings.mongodb_database]
            .documents.find(
                {
                    "filename": {"$in": sorted(allowed_names)},
                    "deleted_at": {"$exists": False},
                    "active_index_version": {"$gt": 0},
                }
            )
            .to_list(length=10)
        )
        if {d["filename"] for d in docs} != allowed_names:
            raise RuntimeError(
                "Expected all three synthetic fixtures to be ingested first"
            )
        # Warm the shared worker once; do not load another model in the API.
        probe = await asyncio.to_thread(embedding_runtime_probe.delay)
        try:
            await asyncio.to_thread(probe.get, timeout=240)
        finally:
            await asyncio.to_thread(probe.forget)
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
            doc["uploaded_by"] = user["_id"]
            doc["department_id"] = user["department_id"]
            doc["visibility"] = "PRIVATE"
            doc["allowed_user_ids"] = []
        await db.documents.insert_many(docs)
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
            assert login.status_code == 200
            token = login.json()["access_token"]
            results = []
            for case in source["cases"]:
                response = await client.post(
                    "/api/v1/retrieval/search",
                    headers={"Authorization": "Bearer " + token},
                    json={"query": case["query"]},
                )
                payload = response.json()
                names = {p["filename"] for p in payload.get("passages", [])}
                expected = set(case["expected"])
                results.append(
                    {
                        "id": case["id"],
                        "http": response.status_code,
                        "status": payload.get(
                            "status", payload.get("error", {}).get("code")
                        ),
                        "recall_at_5": len(names & expected) / len(expected)
                        if expected
                        else None,
                        "precision": len(names & expected) / len(names)
                        if names
                        else 0.0,
                        "negative_empty": not names if not expected else None,
                        "result_count": len(payload.get("passages", [])),
                    }
                )
            positive = [
                r["recall_at_5"] for r in results if r["recall_at_5"] is not None
            ]
            print(
                json.dumps(
                    {
                        "dataset_version": source["version"],
                        "cutoff": settings.retrieval_min_score,
                        "mean_recall_at_5": sum(positive) / len(positive),
                        "cases": results,
                    }
                )
            )
            if any(r["http"] != 200 for r in results):
                raise RuntimeError(
                    "Live search returned a provider/configuration failure"
                )
    finally:
        app.dependency_overrides = previous
        keys = [k async for k in redis.scan_iter(match=name + ":*")]
        if keys:
            await redis.delete(*keys)
        settings.auth_rate_prefix = old_prefix
        await redis.aclose()
        assert name.startswith("test_live_retrieval_")
        await mongo.drop_database(name)
        mongo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    asyncio.run(main(parser.parse_args().dataset))
