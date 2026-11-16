"""Run independent live AI Chat cases on the existing synthetic retrieval suite.

Run inside the API container with the path to the synthetic dataset.
The JSON lines include synthetic answers and cited passages for human grading.
"""

import argparse
import asyncio
import json
import re
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.chat import service
from app.chat.grounding import ABSTENTION
from app.config import settings
from scripts.chat_live_check import main

FILENAME_LOOKUPS = {"exact-support-policy", "exact-training-policy"}
REWRITE = "Who must approve an annual leave request?"


async def local_policy_rewrite(_provider, query):
    """Experimental terminology normalization for the synthetic policy corpus."""
    normalized = query
    for pattern, replacement in (
        (r"\b(?:paid time off|vacation)\b", "annual leave"),
        (r"\bsign-off\b", "approval"),
        (r"\bsupervisor(?:'s|’s) permission\b", "manager approval"),
        (r"\ballowance each year\b", "days per year"),
    ):
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized if normalized != query else None


def emit(result):
    print(json.dumps(result), flush=True)


def stream_events(response):
    events = []
    for frame in response.text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = frame.splitlines()
        if lines and lines[0].startswith("event: "):
            data = "\n".join(line[6:] for line in lines[1:] if line.startswith("data: "))
            events.append((lines[0][7:], json.loads(data)))
    return events


async def check_rewrite(client, headers, case):
    for variant, query in (("original", case["query"]), ("rewrite", REWRITE)):
        response = await client.post(
            "/api/v1/retrieval/search", headers=headers, json={"query": query}
        )
        body = response.json()
        filenames = [p["filename"] for p in body.get("passages", [])]
        emit({
            "kind": "rewrite",
            "variant": variant,
            "http": response.status_code,
            "sources": filenames,
            "expected_source_found": bool(set(filenames) & set(case["expected"])),
        })


async def evaluate(client, headers, cases, pause, *, compare_retry=False):
    if not compare_retry:
        rewrite_case = next(case for case in cases if case["id"] == "paraphrased-time-off")
        await check_rewrite(client, headers, rewrite_case)
    original_retrieve = service.retrieve
    last_creation = None
    failures = 0
    for case in cases:
        if case["id"] in FILENAME_LOOKUPS:
            continue
        if last_creation is not None:
            await asyncio.sleep(max(0, pause - (time.monotonic() - last_creation)))
        last_creation = time.monotonic()
        created = await client.post(
            "/api/v1/conversations", headers=headers, json={"title": "Synthetic answerability"}
        )
        created.raise_for_status()
        cid = created.json()["conversation_id"]
        retrieved = []
        queries = []

        async def observe(db, actor, query, request_id, captured=retrieved, attempts=queries):
            attempts.append(query)
            result = await original_retrieve(db, actor, query, request_id)
            captured.extend(result["passages"])
            return result

        started = time.perf_counter()
        with patch.object(service, "retrieve", observe):
            response = await client.post(
                f"/api/v1/conversations/{cid}/messages/stream",
                headers=headers,
                json={"question": case["query"], "client_request_id": str(uuid4())},
            )
        events = stream_events(response) if response.status_code == 200 else []
        answer = "".join(data.get("text", "") for event, data in events if event == "token")
        citations = [
            citation for event, data in events if event == "citations"
            for citation in data.get("citations", [])
        ]
        completed = bool(events and events[-1][0] == "completed")
        failures += not completed
        emit({
            "kind": "chat",
            "mode": "retry" if settings.chat_query_retry_enabled else "baseline",
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "queries": queries,
            "id": case["id"],
            "expected_sources": case["expected"],
            "http": response.status_code,
            "completed": completed,
            "abstained": completed and answer.strip() == ABSTENTION and not citations,
            "answer": answer if completed else None,
            "cited_sources": [citation["filename"] for citation in citations],
            "passages": [
                {"filename": p["filename"], "text": p["text"]} for p in retrieved
            ],
            "errors": [data.get("code") for event, data in events if event == "error"],
        })
    return int(bool(failures))


async def run(dataset, pause, *, compare_retry=False, mode=None, case_ids=None):
    cases = json.loads(Path(dataset).read_text(encoding="utf-8"))["cases"]
    if case_ids:
        available = {case["id"] for case in cases} - FILENAME_LOOKUPS
        if unknown := set(case_ids) - available:
            raise ValueError(f"Unknown Chat case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in case_ids]
    async def execute(client, headers):
        return await evaluate(client, headers, cases, pause, compare_retry=compare_retry or mode is not None or bool(case_ids))

    with patch.object(settings, "chat_rate_limit", 30):
        if mode is not None:
            with patch.object(settings, "chat_query_retry_enabled", mode == "retry"):
                return await main(execute)
        with patch.object(settings, "chat_query_retry_enabled", False):
            baseline = await main(execute)
        if not compare_retry:
            return baseline
        with patch.object(settings, "chat_query_retry_enabled", True):
            retry = await main(execute)
        return int(bool(baseline or retry))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--pause", type=float, default=8)
    variants = parser.add_mutually_exclusive_group()
    variants.add_argument("--compare-retry", action="store_true")
    variants.add_argument("--mode", choices=("baseline", "retry"))
    parser.add_argument("--case-id", action="append", help="Run only this case; repeat to select multiple")
    parser.add_argument("--local-rewrite", action="store_true", help="Use experimental policy terminology rules")
    args = parser.parse_args()
    if args.pause < 7:
        parser.error("--pause must be at least 7 seconds")
    rewrite = local_policy_rewrite if args.local_rewrite else service.retry_search_query
    with patch.object(service, "retry_search_query", rewrite):
        raise SystemExit(asyncio.run(run(
        args.dataset,
        args.pause,
        compare_retry=args.compare_retry,
        mode=args.mode,
        case_ids=args.case_id,
    )))
