"""Ranking metrics and conservative, predeclared reranking acceptance gates."""

import math
from statistics import median

from scripts.hybrid_evaluation import summarize

PROFILES = {
    "semantic": (False, False),
    "hybrid": (True, False),
    "semantic_reranked": (False, True),
    "hybrid_reranked": (True, True),
}


def ranking_metrics(sources, expected, *, ok=True):
    if not expected:
        return {"reciprocal_rank": None, "ndcg_at_5": None, "top1_relevant": None}
    seen, relevance = set(), []
    for name in sources[:5] if ok else []:
        relevance.append(int(name in expected and name not in seen))
        seen.add(name)
    reciprocal = next(
        (1 / (i + 1) for i, relevant in enumerate(relevance) if relevant), 0
    )
    dcg = sum(value / math.log2(i + 2) for i, value in enumerate(relevance))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(5, len(set(expected)))))
    return {
        "reciprocal_rank": reciprocal,
        "ndcg_at_5": dcg / ideal,
        "top1_relevant": bool(relevance and relevance[0]),
    }


def percentile(values, fraction=0.95):
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def report(cases, contention):
    summary = {}
    for profile in PROFILES:
        value = summarize(cases, profile)
        positives = [case[profile] for case in cases if case["expected"]]
        value.update(
            {
                metric: sum(observation[metric] for observation in positives)
                / len(positives)
                if positives
                else None
                for metric in ("reciprocal_rank", "ndcg_at_5", "top1_relevant")
            }
        )
        value["rerank_completed_requests"] = sum(
            case[profile]["stages"].get("completed", 0) > 0 for case in cases
        )
        value["rerank_fallback_requests"] = sum(
            case[profile]["stages"].get("fallback", 0) > 0 for case in cases
        )
        summary[profile] = value
    comparisons = {}
    for base in ("semantic", "hybrid"):
        reranked = base + "_reranked"
        a, b = summary[base], summary[reranked]
        losses = [
            case["id"]
            for case in cases
            if case["expected"]
            and case[reranked]["recall_at_5"] < case[base]["recall_at_5"]
        ]
        gates = {
            "all_requests_successful": a["request_errors"] == b["request_errors"] == 0,
            "model_exercised": b["rerank_completed_requests"] > 0,
            "no_rerank_fallbacks": b["rerank_fallback_requests"] == 0,
            "no_provider_errors": not any(
                case[profile]["fetch_errors"]
                or case[profile].get("semantic_errors", [])
                for case in cases
                for profile in (base, reranked)
            ),
            "ndcg_improves": a["ndcg_at_5"] is not None
            and b["ndcg_at_5"] > a["ndcg_at_5"],
            "mrr_does_not_regress": a["reciprocal_rank"] is not None
            and b["reciprocal_rank"] >= a["reciprocal_rank"],
            "no_per_question_recall_losses": not losses,
            "irrelevant_passages_do_not_increase": b["irrelevant_passages"]
            <= a["irrelevant_passages"],
            "unsupported_hit_rate_does_not_increase": b[
                "unsupported_questions_with_hits"
            ]
            <= a["unsupported_questions_with_hits"],
            "p95_within_20_percent_plus_250ms": a["latency_ms_p95"] is not None
            and b["latency_ms_p95"] <= a["latency_ms_p95"] * 1.2 + 250,
            "contention_embedding_p95_within_20_percent_plus_250ms": contention[
                "gates"
            ]["embedding_p95_within_budget"],
        }
        comparisons[reranked] = {
            "baseline": base,
            "recall_losses": losses,
            "gates": gates,
            "decision": "eligible_for_representative_acceptance"
            if all(gates.values())
            else "keep_disabled",
        }
    return {
        "summary": summary,
        "comparisons": comparisons,
        "decision": "keep_disabled"
        if any(value["decision"] == "keep_disabled" for value in comparisons.values())
        else "eligible_for_representative_acceptance",
    }


def summarize_jobs(jobs):
    return {
        kind: {
            "jobs": len(rows),
            "errors": sum(not row["valid"] for row in rows),
            "queue_wait_ms_p50": median([row["queue_wait_ms"] for row in rows]),
            "queue_wait_ms_p95": percentile([row["queue_wait_ms"] for row in rows]),
            "execution_ms_p95": percentile([row["execution_ms"] for row in rows]),
            "end_to_end_ms_p95": percentile([row["end_to_end_ms"] for row in rows]),
        }
        for kind in ("embedding", "rerank")
        if (rows := [row for row in jobs if row["kind"] == kind])
    }
