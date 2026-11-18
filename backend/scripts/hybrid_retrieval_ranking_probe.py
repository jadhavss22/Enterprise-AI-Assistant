"""Compare ranking with preloaded live keyword records and paired semantic input."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.retrieval import lexical, service
from app.retrieval.providers import retrieval_index
from scripts import hybrid_retrieval_benchmark as benchmark


async def main(dataset, output):
    original_observe = benchmark.observe
    original_embedding = service.query_embedding
    original_search = service.search_namespaces
    snapshot = {}
    active_case = None
    semantic = {}

    async def snapshot_fetch(documents, _search_index):
        if not snapshot:
            if (
                len(documents) != 3
                or sum(doc["chunk_count"] for doc in documents) > lexical.MAX_CHUNKS
            ):
                raise RuntimeError("Unexpected synthetic scope")
            index = await asyncio.to_thread(retrieval_index)
            loaded = {}
            for doc in documents:
                version = doc["active_index_version"]
                key = (doc["_id"], version)
                ids = [
                    hashlib.sha256(
                        f"{doc['_id']}:{version}:{chunk}".encode()
                    ).hexdigest()
                    for chunk in range(doc["chunk_count"])
                ]
                response = await asyncio.to_thread(
                    index.fetch,
                    ids=ids,
                    namespace=f"{doc['_id']}-v{version}",
                    timeout=20,
                )
                vectors = (
                    response["vectors"]
                    if isinstance(response, dict)
                    else response.vectors
                )
                loaded[key] = [vectors[vector_id] for vector_id in ids]
            snapshot.update(loaded)
            print(
                f"Preloaded {sum(map(len, snapshot.values()))} active synthetic records",
                flush=True,
            )
        return [
            (doc, vector)
            for doc in documents
            for vector in snapshot.get((doc["_id"], doc["active_index_version"]), [])
        ]

    async def cached_embedding(query):
        if "vector" not in semantic:
            semantic["vector"] = await original_embedding(query)
        return semantic["vector"]

    async def cached_search(documents, vector):
        if "matches" not in semantic:
            semantic["matches"] = await original_search(documents, vector)
        return semantic["matches"]

    async def observe(client, token, case, hybrid):
        nonlocal active_case
        if case["id"] != active_case:
            semantic.clear()
            active_case = case["id"]
        return await original_observe(client, token, case, hybrid)

    output = Path(output)
    with (
        patch.object(settings, "retrieval_min_score", 0.25),
        patch.object(settings, "retrieval_rate_limit", 120),
        patch.object(lexical, "fetch_active_chunks", snapshot_fetch),
        patch.object(service, "query_embedding", cached_embedding),
        patch.object(service, "search_namespaces", cached_search),
        patch.object(benchmark, "observe", observe),
    ):
        await benchmark.main(
            dataset, output, db_url=settings.mongodb_url, redis_url=settings.redis_url
        )
    result = json.loads(output.read_text(encoding="utf-8"))
    if len(snapshot) != 3 or any(
        pair["hybrid"]["fetch_errors"] for pair in result["cases"]
    ):
        raise RuntimeError("Could not preload all three synthetic fixtures")
    result["mode"] = "preloaded-ranking-diagnostic"
    del result["paired_latency_delta_ms_p50"]
    for section in ("summary", "paired_summary"):
        for summary in result.get(section, {}).values():
            for field in tuple(summary):
                if field.startswith(("latency_", "fetch_", "cases_with_fetch")):
                    del summary[field]
    for pair in result["cases"]:
        for mode in ("semantic", "hybrid"):
            for field in ("latency_ms", "fetches", "fetch_errors"):
                del pair[mode][field]
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"]), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.output))
