"""Live four-profile ranking and single-worker contention evaluation."""

import argparse
import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.retrieval import quality, service
from app.retrieval.reranker import MODEL, REVISION
from app.retrieval.tasks import rerank_passages
from app.workers.celery_app import celery_app
from scripts.hybrid_expanded_benchmark import compare, save, worker_result
from scripts.hybrid_retrieval_benchmark import observe
from scripts.rerank_eval_tasks import measured_job
from scripts.rerank_evaluation import PROFILES, ranking_metrics, report, summarize_jobs


class Stages(logging.Handler):
    def __init__(self):
        super().__init__()
        self.counts = {"completed": 0, "fallback": 0}

    def emit(self, record):
        message = str(record.msg)
        if message.startswith("retrieval.rerank_completed"):
            self.counts["completed"] += 1
        elif message.startswith("retrieval.rerank_fallback"):
            self.counts["fallback"] += 1


async def observe_profile(client, token, case, profile):
    hybrid, rerank = PROFILES[profile]
    stages, candidate_orders, errors = Stages(), [], []
    original_rank, original_search = (
        quality.rerank_candidates,
        service.search_namespaces,
    )

    async def ranking(query, candidates, filenames, request_id):
        ranked = await original_rank(query, candidates, filenames, request_id)
        candidate_orders.append(
            {
                "before": [filenames[row["document_id"]] for row in candidates],
                "after": [filenames[row["document_id"]] for row in ranked],
                "count": len(candidates),
            }
        )
        return ranked

    async def search(*args):
        try:
            return await original_search(*args)
        except Exception as exc:
            errors.append(type(exc).__name__)
            raise

    previous_level = quality.logger.level
    quality.logger.addHandler(stages)
    quality.logger.setLevel(logging.INFO)
    try:
        with (
            patch.object(settings, "retrieval_rerank_enabled", rerank),
            patch.object(quality, "rerank_candidates", ranking),
            patch.object(service, "search_namespaces", search),
        ):
            result = await observe(client, token, case, hybrid)
    finally:
        quality.logger.removeHandler(stages)
        quality.logger.setLevel(previous_level)
    result.update(
        ranking_metrics(
            result["returned_sources"], case["expected"], ok=result["http"] == 200
        )
    )
    result.update(
        stages=stages.counts, candidate_orders=candidate_orders, semantic_errors=errors
    )
    return result


async def contention(source):
    # Worst permitted candidate count, bounded short synthetic passages.
    texts = [doc["text"] for doc in source["documents"]]
    pairs = [
        ["Which policy defines annual leave?", texts[i % len(texts)]]
        for i in range(settings.retrieval_candidate_limit)
    ]
    query = "How many annual leave days are provided?"

    async def job(kind):
        submitted = time.time() * 1000
        return await worker_result(
            measured_job, kind, query if kind == "embedding" else pairs, submitted
        )

    isolated = []
    for kind in ("embedding", "rerank"):
        for _ in range(12):
            isolated.append(await job(kind))
    mixed = []
    for wave in range(6):
        order = (
            ["rerank"] * 3 + ["embedding"]
            if wave % 2 == 0
            else ["embedding"] + ["rerank"] * 3
        )
        # Publish in a known order; submission returns after the broker accepts.
        tasks = []
        for ordinal, kind in enumerate(order):
            submitted = time.time() * 1000
            task = await asyncio.to_thread(
                measured_job.apply_async,
                args=[kind, query if kind == "embedding" else pairs, submitted],
                argsrepr="[<synthetic contention job>]",
                retry=False,
            )
            tasks.append((task, wave, ordinal))
        try:
            values = await asyncio.gather(
                *(asyncio.to_thread(task.get, timeout=60) for task, _, _ in tasks)
            )
            mixed.extend(
                dict(value, wave=wave, submission_order=ordinal)
                for value, (_, wave, ordinal) in zip(values, tasks, strict=True)
            )
        finally:
            for task, _, _ in tasks:
                await asyncio.to_thread(task.forget)
    a, b = summarize_jobs(isolated), summarize_jobs(mixed)
    return {
        "description": "12 isolated jobs per kind; six four-job waves, alternating three reranks before/after one embedding; single worker",
        "candidate_pairs": len(pairs),
        "isolated": a,
        "mixed": b,
        "jobs": {"isolated": isolated, "mixed": mixed},
        "gates": {
            "embedding_p95_within_budget": not any(
                not job["valid"] for job in isolated + mixed
            )
            and b["embedding"]["end_to_end_ms_p95"]
            <= a["embedding"]["end_to_end_ms_p95"] * 1.2 + 250
        },
    }


async def experiment(client, session, source, result, output, rounds):
    result.update(
        mode="live_four_profile_reranking",
        model={
            "name": MODEL,
            "revision": REVISION,
            "device": "cpu",
            "max_length": 384,
            "batch_size": 8,
            "safetensors": True,
            "cache_only_inference": True,
        },
        worker_configuration={
            "concurrency": 1,
            "pool": "prefork",
            "queue": "rerank-evaluation",
            "OMP_NUM_THREADS": 1,
            "MKL_NUM_THREADS": 1,
        },
    )
    result["settings"]["reranking"] = "per_profile"
    result["settings"]["rerank_timeout_seconds"] = (
        settings.retrieval_rerank_timeout_seconds
    )
    for _ in range(60):
        probe = await worker_result(
            rerank_passages, [["synthetic readiness", "synthetic readiness"]]
        )
        if isinstance(probe, dict) and "scores" in probe:
            break
        await asyncio.sleep(1)
    else:
        raise RuntimeError("Pinned reranker failed readiness")
    profiles = list(PROFILES)
    result["warmups"] = {}
    for profile in profiles:
        warmup = await observe_profile(
            client, await session.access_token(), source["cases"][0], profile
        )
        result["warmups"][profile] = warmup
        if warmup["http"] != 200 or (
            PROFILES[profile][1] and not warmup["stages"]["completed"]
        ):
            raise RuntimeError("Model-backed profile warmup failed")
    for repeat in range(rounds):
        for ordinal, case in enumerate(source["cases"]):
            offset = (ordinal + repeat) % len(profiles)
            order = profiles[offset:] + profiles[:offset]
            row = dict(case, round=repeat + 1, profile_order=order)
            for profile in order:
                row[profile] = await observe_profile(
                    client, await session.access_token(), case, profile
                )
            result["cases"].append(row)
            result["session_refreshes"] = session.refreshes
            save(output, result)
            print(
                f"{len(result['cases'])}/{rounds * len(source['cases'])} {case['id']}: "
                + ", ".join(f"{p}={row[p]['http']}" for p in profiles),
                flush=True,
            )
    print("Measuring shared-worker contention", flush=True)
    result["contention"] = await contention(source)
    result.update(report(result["cases"], result["contention"]))
    if any(
        result["summary"][profile]["rerank_completed_requests"] == 0
        for profile in ("semantic_reranked", "hybrid_reranked")
    ):
        raise RuntimeError("Reranking was not exercised")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=1)
    parser.add_argument("--allow-synthetic-writes", action="store_true", required=True)
    args = parser.parse_args()
    celery_app.conf.task_routes = {"*": {"queue": "rerank-evaluation"}}
    with (
        patch.object(settings, "retrieval_rate_limit", 10000),
        patch.object(settings, "retrieval_query_rewrite_enabled", False),
        patch.object(settings, "retrieval_rerank_enabled", False),
    ):
        asyncio.run(
            compare(args.dataset, args.output, args.rounds, experiment=experiment)
        )
