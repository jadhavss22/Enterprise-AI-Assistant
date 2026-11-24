"""Validated labels, unbiased denominators, and predeclared hybrid gates."""

import hashlib
import json
import math
import time
from statistics import median


def dataset_sha256(path):
    """Fingerprint UTF-8 labels with normalized LF newlines across checkouts."""
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


class EvaluationSession:
    """Refresh through the public cookie/CSRF flow before a timed observation."""

    def __init__(self, client, token, origin, ttl, *, clock=time.monotonic):
        self.client, self.token, self.origin, self.ttl = client, token, origin, ttl
        self.clock = clock
        self.refresh_at = clock() + max(0, ttl - 60)
        self.refreshes = 0

    async def access_token(self):
        if self.clock() >= self.refresh_at:
            headers = {"Origin": self.origin}
            csrf = await self.client.get("/api/v1/auth/csrf", headers=headers)
            if csrf.status_code != 200:
                raise RuntimeError("Evaluation CSRF refresh failed")
            refreshed = await self.client.post(
                "/api/v1/auth/refresh",
                headers=headers | {"X-CSRF-Token": csrf.json()["csrf_token"]},
            )
            if refreshed.status_code != 200:
                raise RuntimeError("Evaluation session refresh failed")
            self.token = refreshed.json()["access_token"]
            self.refresh_at = self.clock() + max(0, self.ttl - 60)
            self.refreshes += 1
        return self.token


def validate_output(dataset, output):
    if dataset.resolve() == output.resolve():
        raise ValueError("Output must not overwrite the labeled dataset")
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("cleanup") != "completed":
            raise ValueError(
                "Resolve pending resource cleanup before replacing this journal"
            )


def validate_dataset(source):
    documents, cases = source["documents"], source["cases"]
    names = {doc["filename"] for doc in documents}
    if not documents or len(names) != len(documents) or len(documents) > 25:
        raise ValueError("Require 1..25 uniquely named synthetic documents")
    if any(not name.startswith("synthetic-") for name in names):
        raise ValueError("Only explicitly synthetic sources are allowed")
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Require unique case IDs")
    texts = {doc["filename"]: doc["text"] for doc in documents}
    for case in cases:
        if not case["query"].strip() or not case["category"] or not case["rationale"]:
            raise ValueError("Every case needs a query, category, and label rationale")
        if len(case["expected"]) != len(set(case["expected"])):
            raise ValueError("Duplicate expected sources")
        if not set(case["expected"]) <= names:
            raise ValueError("Unknown expected source")
        if bool(case["expected"]) != bool(case["evidence"]):
            raise ValueError(
                "Supported questions require evidence; negatives cannot have it"
            )
        for name in case["expected"]:
            if not any(
                evidence.casefold() in texts[name].casefold()
                for evidence in case["evidence"]
            ):
                raise ValueError(
                    "Expected evidence must occur in each relevant document"
                )


def summarize(pairs, mode):
    observations = [pair[mode] for pair in pairs]
    supported = [pair for pair in pairs if pair["expected"]]
    unsupported = [pair for pair in pairs if not pair["expected"]]
    ok = [item for item in observations if item["http"] == 200]
    latencies = sorted(item["latency_ms"] for item in observations)
    returned = sum(len(item["returned_sources"]) for item in ok)
    irrelevant = sum(item["irrelevant_passages"] for item in ok)
    fetches = [fetch for item in observations for fetch in item["fetches"]]
    units = [fetch["read_units"] for fetch in fetches if "read_units" in fetch]
    return {
        "requests": len(observations),
        "http_200": len(ok),
        "request_errors": len(observations) - len(ok),
        "supported_questions": len(supported),
        # Provider errors count as recall zero, never disappear from denominators.
        "mean_document_recall_at_5": sum(
            pair[mode]["recall_at_5"] if pair[mode]["http"] == 200 else 0
            for pair in supported
        )
        / len(supported)
        if supported
        else None,
        "unsupported_questions": len(unsupported),
        "unsupported_questions_with_hits": sum(
            pair[mode]["http"] == 200 and bool(pair[mode]["returned_sources"])
            for pair in unsupported
        ),
        "unsupported_false_positive_rate": sum(
            pair[mode]["http"] == 200 and bool(pair[mode]["returned_sources"])
            for pair in unsupported
        )
        / len(unsupported)
        if unsupported
        else None,
        "returned_passages": returned,
        "irrelevant_passages": irrelevant,
        "passage_precision": (returned - irrelevant) / returned if returned else None,
        "latency_ms_p50": median(latencies) if latencies else None,
        "latency_ms_p95": latencies[math.ceil(0.95 * len(latencies)) - 1]
        if latencies
        else None,
        "keyword_timeout_requests": sum(
            "TimeoutError" in item["fetch_errors"] for item in observations
        ),
        "semantic_timeout_requests": sum(
            "TimeoutError" in item.get("semantic_errors", []) for item in observations
        ),
        "keyword_error_requests": sum(
            bool(item["fetch_errors"]) for item in observations
        ),
        "fetch_calls": len(fetches),
        "fetch_records_requested": sum(fetch["requested"] for fetch in fetches),
        "fetch_records_returned": sum(fetch.get("returned", 0) for fetch in fetches),
        "fetch_read_units_reported": sum(units) if units else None,
        "fetch_calls_with_usage": len(units),
    }


def report(pairs):
    summary = {mode: summarize(pairs, mode) for mode in ("semantic", "hybrid")}
    categories = {
        category: {
            mode: summarize([p for p in pairs if p["category"] == category], mode)
            for mode in summary
        }
        for category in sorted({p["category"] for p in pairs})
    }
    regressions = {
        "recall_losses": [
            p["id"]
            for p in pairs
            if p["expected"]
            and (p["hybrid"]["recall_at_5"] or 0) < (p["semantic"]["recall_at_5"] or 0)
        ],
        "new_unsupported_hits": [
            p["id"]
            for p in pairs
            if not p["expected"]
            and not p["semantic"]["returned_sources"]
            and p["hybrid"]["returned_sources"]
        ],
    }
    semantic, hybrid = summary["semantic"], summary["hybrid"]
    gates = {
        "all_requests_successful": all(
            value["request_errors"] == 0 for value in summary.values()
        ),
        "keyword_leg_exercised": hybrid["fetch_calls"] > 0,
        "recall_improves": hybrid["mean_document_recall_at_5"] is not None
        and semantic["mean_document_recall_at_5"] is not None
        and hybrid["mean_document_recall_at_5"] > semantic["mean_document_recall_at_5"],
        "no_per_question_recall_loss": not regressions["recall_losses"],
        "no_new_unsupported_hits": not regressions["new_unsupported_hits"],
        "irrelevant_passages_do_not_increase": hybrid["irrelevant_passages"]
        <= semantic["irrelevant_passages"],
        "p95_within_20_percent_plus_250ms": hybrid["latency_ms_p95"] is not None
        and hybrid["latency_ms_p95"] <= semantic["latency_ms_p95"] * 1.2 + 250,
        "no_keyword_errors_or_timeouts": hybrid["keyword_error_requests"] == 0,
        "no_semantic_timeout_increase": hybrid["semantic_timeout_requests"]
        <= semantic["semantic_timeout_requests"],
    }
    return {
        "summary": summary,
        "by_category": categories,
        "regressions": regressions,
        "gates": gates,
        "decision": "eligible_for_representative_acceptance"
        if all(gates.values())
        else "keep_disabled",
    }
