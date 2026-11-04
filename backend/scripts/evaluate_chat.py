"""Opt-in synthetic generation evaluation. Stops at first provider outage/quota failure.

Does not test live retrieval or permissions. Output contains only synthetic answers.
Term matching is a review aid, not factual-entailment verification or acceptance.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.chat.grounding import (
    ABSTENTION,
    build_grounded_messages,
    validate_answer_references,
)
from app.chat.provider import ChatProviderError, GeminiProvider
from app.config import Settings


def quota_metadata(exc):
    # Extract ONLY bounded retry seconds and a fixed quota-scope enum. Never
    # serialize upstream messages, project IDs, API keys or arbitrary body fields.
    identifiers = []
    retry = exc.retry_after
    body = getattr(exc.__context__, "body", {})
    def visit(node):
        nonlocal retry
        if isinstance(node, dict):
            value = node.get("quotaId")
            if isinstance(value, str):
                identifiers.append(value.lower())
            delay = node.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    seconds = float(delay[:-1])
                    if 0 < seconds <= 3600:
                        retry = max(1, int(seconds))
                except ValueError:
                    pass
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(body)
    scope = "DAILY" if any("perday" in value for value in identifiers) else "PER_MINUTE" if any("perminute" in value for value in identifiers) else "UNKNOWN"
    return {"quota_scope": scope, "retry_after": retry}


async def run(output, resume=False, pause_seconds=15):
    root = Path(__file__).resolve().parents[2]
    dataset = json.loads(
        (root / "evaluation/chat/cases.json").read_text(encoding="utf-8")
    )
    provider = GeminiProvider(Settings(_env_file=root / ".env"))
    rows = []
    if resume and Path(output).exists():
        previous = json.loads(Path(output).read_text(encoding="utf-8"))
        if previous["dataset_version"] != dataset["version"]:
            raise ValueError("Cannot resume a different dataset version")
        rows = [row for row in previous["results"] if "answer" in row]
    completed_ids = {row["id"] for row in rows}
    for case in dataset["cases"]:
        if case["id"] in completed_ids:
            continue
        messages, references = build_grounded_messages(
            case["question"],
            [
                {
                    "document_id": "0" * 24,
                    "index_version": 1,
                    "chunk_id": "synthetic",
                    "chunk_index": 0,
                    "filename": "synthetic.txt",
                    "page": None,
                    "source_path": "/documents/" + "0" * 24 + "/source",
                    "text": case["evidence"],
                }
            ],
        )
        try:
            answer = await provider.complete(messages)
        except ChatProviderError as exc:
            rows.append({"id": case["id"], "provider_error": exc.code, **quota_metadata(exc)})
            break
        try:
            citations = validate_answer_references(answer, references)
            reference_integrity = True
        except ChatProviderError:
            citations, reference_integrity = [], False
        rows.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "answer": answer,
                "reference_integrity": reference_integrity,
                "citation_count": len(citations),
                "abstained": answer.strip() == ABSTENTION,
                "review_terms_present": all(
                    term.lower() in answer.lower() for term in case["review_terms"]
                ),
                "manual_claim_support_review": "PENDING",
            }
        )
        await asyncio.sleep(pause_seconds)
    result = {
        "dataset_version": dataset["version"],
        "results": rows,
        "acceptance": "PENDING_MANUAL_REVIEW",
        "completed_cases": sum("answer" in row for row in rows),
        "total_cases": len(dataset["cases"]),
    }
    Path(output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "results"}))
    return 1 if any("provider_error" in row for row in rows) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly authorize up to 30 synthetic API calls",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pause-seconds", type=int, default=15)
    parser.add_argument("--output", default="chat-evaluation-results.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Use --live only after the streaming smoke test passes")
    raise SystemExit(
        asyncio.run(run(args.output, args.resume, max(15, args.pause_seconds)))
    )
