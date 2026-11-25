import math

import pytest

from scripts.rerank_evaluation import PROFILES, ranking_metrics, report, summarize_jobs


def observation(sources, expected, *, rerank=False, http=200):
    return {
        "http": http,
        "returned_sources": sources,
        "recall_at_5": len(set(sources) & set(expected)) / len(expected)
        if expected
        else None,
        "irrelevant_passages": sum(name not in expected for name in sources),
        "latency_ms": 100,
        "fetches": [],
        "fetch_errors": [],
        "semantic_errors": [],
        "stages": {"completed": int(rerank), "fallback": 0},
        **ranking_metrics(sources, expected, ok=http == 200),
    }


def sample():
    row = {"id": "supported", "expected": ["relevant"], "category": "identifier"}
    for profile in PROFILES:
        reranked = profile.endswith("reranked")
        row[profile] = observation(
            ["relevant", "wrong"] if reranked else ["wrong", "relevant"],
            row["expected"],
            rerank=reranked,
        )
    return [row]


def test_rank_quality_improvement_is_separate_from_recall():
    result = report(sample(), {"gates": {"embedding_p95_within_budget": True}})
    assert result["summary"]["semantic"]["mean_document_recall_at_5"] == 1
    assert result["summary"]["semantic_reranked"]["mean_document_recall_at_5"] == 1
    assert result["summary"]["semantic"]["reciprocal_rank"] == 0.5
    assert result["summary"]["semantic_reranked"]["reciprocal_rank"] == 1
    assert result["decision"] == "eligible_for_representative_acceptance"


def test_duplicates_cannot_inflate_document_ndcg():
    result = ranking_metrics(["a", "a"], ["a", "b"])
    assert result["ndcg_at_5"] == pytest.approx(1 / (1 + 1 / math.log2(3)))
    assert ranking_metrics(["a"], ["a"], ok=False)["reciprocal_rank"] == 0
    assert ranking_metrics(["a"], [])["ndcg_at_5"] is None


def test_contention_and_silent_fallback_block_ranking_acceptance():
    rows = sample()
    rows[0]["semantic_reranked"]["stages"] = {"completed": 0, "fallback": 1}
    result = report(rows, {"gates": {"embedding_p95_within_budget": False}})
    assert result["decision"] == "keep_disabled"
    assert not result["comparisons"]["semantic_reranked"]["gates"]["model_exercised"]
    assert not result["comparisons"]["hybrid_reranked"]["gates"][
        "contention_embedding_p95_within_20_percent_plus_250ms"
    ]


def test_recall_losses_and_provider_errors_cannot_disappear():
    rows = sample()
    rows[0]["semantic_reranked"] = observation([], ["relevant"], http=503)
    result = report(rows, {"gates": {"embedding_p95_within_budget": True}})
    assert result["summary"]["semantic_reranked"]["ndcg_at_5"] == 0
    assert result["comparisons"]["semantic_reranked"]["recall_losses"] == ["supported"]
    assert not result["comparisons"]["semantic_reranked"]["gates"][
        "all_requests_successful"
    ]


def test_job_summary_retains_queue_delay_and_errors():
    jobs = [
        {
            "kind": "embedding",
            "valid": valid,
            "queue_wait_ms": delay,
            "execution_ms": 10,
            "end_to_end_ms": delay + 10,
        }
        for delay, valid in ((0, True), (500, False))
    ]
    summary = summarize_jobs(jobs)["embedding"]
    assert summary["errors"] == 1
    assert summary["queue_wait_ms_p95"] == 500
    assert summary["end_to_end_ms_p95"] == 510


@pytest.mark.asyncio
async def test_custom_experiment_retains_exclusive_journal_lifecycle(
    monkeypatch, tmp_path
):
    from scripts import hybrid_expanded_benchmark as runner

    callback = object()

    async def compare(dataset, output, rounds, *, experiment):
        assert experiment is callback
        assert output.with_suffix(".json.lock").exists()
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runner, "_compare", compare)
    output = tmp_path / "result.json"
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await runner.compare(tmp_path / "labels.json", output, 1, experiment=callback)
    assert not output.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("profile", list(PROFILES))
@pytest.mark.parametrize("field", ["fetch_errors", "semantic_errors"])
def test_http_200_provider_fallback_blocks_acceptance(profile, field):
    rows = sample()
    rows[0][profile][field] = ["TimeoutError"]
    result = report(rows, {"gates": {"embedding_p95_within_budget": True}})
    comparison = profile if profile.endswith("reranked") else profile + "_reranked"
    assert result["comparisons"][comparison]["gates"]["all_requests_successful"]
    assert not result["comparisons"][comparison]["gates"]["no_provider_errors"]
    assert result["decision"] == "keep_disabled"


@pytest.mark.parametrize("empty", [False, True])
def test_missing_supported_labels_cannot_pass_or_crash(empty):
    rows = (
        []
        if empty
        else [
            {
                "id": "unsupported",
                "expected": [],
                "category": "negative",
                **{
                    profile: observation([], [], rerank=profile.endswith("reranked"))
                    for profile in PROFILES
                },
            }
        ]
    )
    result = report(rows, {"gates": {"embedding_p95_within_budget": True}})
    assert result["decision"] == "keep_disabled"
    assert all(
        not value["gates"]["ndcg_improves"] for value in result["comparisons"].values()
    )
