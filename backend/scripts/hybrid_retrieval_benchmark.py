"""Paired live semantic and hybrid evaluation on three existing synthetic fixtures."""

import argparse
import asyncio
import json
import secrets
import time
from functools import partial
from pathlib import Path
from statistics import median
from unittest.mock import patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import get_db, get_redis
from app.config import settings
from app.main import app
from app.retrieval import lexical
from app.workers.celery_app import embedding_runtime_probe

CONTROLS = [
    (
        "literal-employee-manager",
        "approval from the employee's manager",
        ["ingestion-smoke-test.txt"],
    ),
    (
        "literal-helpdesk-hours",
        "Monday through Friday, 9 AM to 6 PM",
        ["ingestion-smoke-test.txt"],
    ),
    (
        "literal-training-credits",
        "1200 demo credits per year",
        ["qa-training-policy.docx"],
    ),
    (
        "literal-training-reimbursement",
        "reimburses fictional training costs",
        ["qa-training-policy.docx"],
    ),
    (
        "near-negative-training-appeal",
        "What is the appeal process for fictional training costs?",
        [],
    ),
    (
        "near-negative-manager-escalation",
        "How do I escalate a rejected request to the employee's manager?",
        [],
    ),
]


async def observe(client, token, case, hybrid):
    fetches = []
    fetch_errors = []
    original_fetch = lexical.fetch_active_chunks

    class ObservedIndex:
        def __init__(self, index):
            self.index = index

        def fetch(self, **kwargs):
            observation = {"requested": len(kwargs["ids"])}
            fetches.append(observation)
            result = self.index.fetch(**kwargs)
            vectors = result["vectors"] if isinstance(result, dict) else result.vectors
            observation["returned"] = len(vectors)
            usage = (
                result.get("usage")
                if isinstance(result, dict)
                else getattr(result, "usage", None)
            )
            units = (
                usage.get("read_units")
                if isinstance(usage, dict)
                else getattr(usage, "read_units", None)
            )
            if isinstance(units, int):
                observation["read_units"] = units
            return result

    async def observed_fetch(documents, index):
        try:
            return await original_fetch(documents, ObservedIndex(index))
        except Exception as exc:
            fetch_errors.append(type(exc).__name__)
            raise

    with (
        patch.object(settings, "retrieval_hybrid_enabled", hybrid),
        patch.object(lexical, "fetch_active_chunks", observed_fetch),
    ):
        started = time.perf_counter()
        response = await client.post(
            "/api/v1/retrieval/search",
            headers={"Authorization": "Bearer " + token},
            json={"query": case["query"]},
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
    payload = response.json()
    sources = [passage["filename"] for passage in payload.get("passages", [])]
    expected = set(case["expected"])
    return {
        "http": response.status_code,
        "status": payload.get("status", payload.get("error", {}).get("code")),
        "latency_ms": latency_ms,
        "returned_sources": sources,
        "recall_at_5": len(set(sources) & expected) / len(expected)
        if expected
        else None,
        "irrelevant_passages": sum(name not in expected for name in sources),
        "fetches": fetches,
        "fetch_errors": fetch_errors,
    }


def summarize(pairs, mode):
    observations = [pair[mode] for pair in pairs]
    passed = [item for item in observations if item["http"] == 200]
    positive = [item for item in passed if item["recall_at_5"] is not None]
    negative = [item for item in passed if item["recall_at_5"] is None]
    latency = sorted(item["latency_ms"] for item in passed)
    fetches = [fetch for item in observations for fetch in item["fetches"]]
    units = [fetch["read_units"] for fetch in fetches if "read_units" in fetch]
    return {
        "http_200": len(passed),
        "positive_cases": len(positive),
        "mean_document_recall_at_5": round(
            sum(item["recall_at_5"] for item in positive) / len(positive), 4
        )
        if positive
        else None,
        "full_recall_cases": sum(item["recall_at_5"] == 1 for item in positive),
        "negative_cases": len(negative),
        "negative_cases_with_hits": sum(
            bool(item["returned_sources"]) for item in negative
        ),
        "positive_cases_with_irrelevant_hits": sum(
            bool(item["irrelevant_passages"]) for item in positive
        ),
        "irrelevant_passages": sum(item["irrelevant_passages"] for item in passed),
        "latency_ms_p50": median(latency) if latency else None,
        "latency_ms_p95": latency[(95 * len(latency) + 99) // 100 - 1]
        if latency
        else None,
        "fetch_calls": len(fetches),
        "fetch_records_requested": sum(fetch["requested"] for fetch in fetches),
        "fetch_records_returned": sum(fetch.get("returned", 0) for fetch in fetches),
        "fetch_read_units_reported": sum(units) if units else None,
        "fetch_calls_with_read_units": len(units),
        "cases_with_fetch_errors": sum(
            bool(item["fetch_errors"]) for item in observations
        ),
    }


def paired_summary(pairs):
    matched = [
        pair
        for pair in pairs
        if pair["semantic"]["http"] == pair["hybrid"]["http"] == 200
    ]
    return {mode: summarize(matched, mode) for mode in ("semantic", "hybrid")}


async def main(dataset, output, *, db_url, redis_url):
    source = json.loads(Path(dataset).read_text())
    allowed_names = {
        "ingestion-smoke-test.txt",
        "qa-support-policy.pdf",
        "qa-training-policy.docx",
    }
    mongo = AsyncIOMotorClient(db_url, tz_aware=True, serverSelectionTimeoutMS=5000)
    redis = Redis.from_url(redis_url, decode_responses=True)
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
        if (
            len(docs) != len(allowed_names)
            or {d["filename"] for d in docs} != allowed_names
        ):
            raise RuntimeError(
                "Expected all three synthetic fixtures to be ingested first"
            )
        # Warm the shared worker once; do not load another model in the API.
        print("Warming the isolated embedding worker", flush=True)
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
            cases = source["cases"] + [
                {"id": case_id, "query": query, "expected": expected}
                for case_id, query, expected in CONTROLS
            ]
            run = partial(observe, client, token)
            for hybrid in (False, True):
                warmup = await run(cases[0], hybrid)
                if warmup["http"] != 200:
                    raise RuntimeError(f"Warmup failed: {warmup['status']}")
                print(
                    f"Warmup hybrid={hybrid}: {warmup['latency_ms']} ms, fetches {warmup['fetches']}, errors {warmup['fetch_errors']}",
                    flush=True,
                )
            pairs = []
            for index, case in enumerate(cases):
                pair = {**case, "first_mode": "hybrid" if index % 2 else "semantic"}
                for hybrid in (True, False) if index % 2 else (False, True):
                    mode = "hybrid" if hybrid else "semantic"
                    pair[mode] = await run(case, hybrid)
                pairs.append(pair)
                print(
                    f"{index + 1}/{len(cases)} {case['id']}: "
                    f"{pair['semantic']['status']} {pair['semantic']['latency_ms']} ms / "
                    f"{pair['hybrid']['status']} {pair['hybrid']['latency_ms']} ms",
                    flush=True,
                )
            paired_latencies = [
                pair["hybrid"]["latency_ms"] - pair["semantic"]["latency_ms"]
                for pair in pairs
                if pair["hybrid"]["http"] == pair["semantic"]["http"] == 200
            ]
            result = {
                "mode": "live",
                "dataset": Path(dataset).name,
                "semantic_cutoff": settings.retrieval_min_score,
                "result_limit": settings.retrieval_result_limit,
                "fixtures": {doc["filename"]: doc["chunk_count"] for doc in docs},
                "summary": {
                    mode: summarize(pairs, mode) for mode in ("semantic", "hybrid")
                },
                "paired_summary": paired_summary(pairs),
                "paired_latency_delta_ms_p50": median(paired_latencies)
                if paired_latencies
                else None,
                "cases": pairs,
            }
            Path(output).write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(result["summary"]), flush=True)
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
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with (
        patch.object(settings, "retrieval_min_score", 0.25),
        patch.object(settings, "retrieval_rate_limit", 120),
    ):
        asyncio.run(
            main(
                args.dataset,
                args.output,
                db_url=settings.mongodb_url,
                redis_url=settings.redis_url,
            )
        )
