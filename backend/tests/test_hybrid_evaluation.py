import asyncio
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.hybrid_evaluation import (
    EvaluationSession,
    report,
    summarize,
    validate_dataset,
    validate_output,
)
from scripts.hybrid_expanded_benchmark import provision_vector, save


def observation(
    *,
    sources=(),
    recall=None,
    http=200,
    irrelevant=0,
    latency=100,
    fetches=(),
    errors=(),
):
    return {
        "http": http,
        "returned_sources": list(sources),
        "recall_at_5": recall,
        "irrelevant_passages": irrelevant,
        "latency_ms": latency,
        "fetches": list(fetches),
        "fetch_errors": list(errors),
    }


def cases():
    fetch = {"requested": 1, "returned": 1, "read_units": 1}
    return [
        {
            "id": "supported",
            "category": "identifier",
            "expected": ["synthetic-a"],
            "semantic": observation(recall=0),
            "hybrid": observation(sources=["synthetic-a"], recall=1, fetches=[fetch]),
        },
        {
            "id": "negative",
            "category": "unsupported_near",
            "expected": [],
            "semantic": observation(),
            "hybrid": observation(fetches=[fetch]),
        },
    ]


def test_provider_errors_remain_in_recall_and_latency_denominators():
    pairs = cases()
    failed = copy.deepcopy(pairs[0])
    failed["id"] = "failed"
    failed["hybrid"] = observation(http=503, recall=0, latency=900)
    summary = summarize(pairs + [failed], "hybrid")
    assert summary["mean_document_recall_at_5"] == 0.5
    assert summary["request_errors"] == 1
    assert summary["latency_ms_p95"] == 900
    assert not report(pairs + [failed])["gates"]["all_requests_successful"]


def test_new_unsupported_match_blocks_enablement_despite_recall_gain():
    pairs = cases()
    pairs[1]["hybrid"] = observation(
        sources=["synthetic-a"], irrelevant=1, fetches=[{"requested": 1}]
    )
    result = report(pairs)
    assert result["gates"]["recall_improves"]
    assert result["regressions"]["new_unsupported_hits"] == ["negative"]
    assert result["summary"]["hybrid"]["passage_precision"] == 0.5
    assert result["decision"] == "keep_disabled"


def test_unknown_usage_is_not_reported_as_zero_and_timeout_blocks_gate():
    pairs = cases()
    pairs[0]["hybrid"].update(
        fetches=[{"requested": 12}], fetch_errors=["TimeoutError"]
    )
    pairs[1]["hybrid"]["fetches"] = []
    result = report(pairs)
    assert result["summary"]["hybrid"]["fetch_read_units_reported"] is None
    assert result["summary"]["hybrid"]["fetch_records_requested"] == 12
    assert result["summary"]["hybrid"]["keyword_timeout_requests"] == 1
    assert result["decision"] == "keep_disabled"


def test_per_question_recall_loss_cannot_hide_behind_average_gain():
    pairs = cases()
    lost = copy.deepcopy(pairs[0])
    lost.update(
        id="lost",
        semantic=observation(sources=["synthetic-a"], recall=1),
        hybrid=observation(recall=0),
    )
    assert report(pairs + [lost])["regressions"]["recall_losses"] == ["lost"]


def test_passing_synthetic_gates_only_allows_representative_acceptance():
    assert report(cases())["decision"] == "eligible_for_representative_acceptance"


def test_committed_expanded_labels_are_complete_and_balanced():
    path = (
        Path(__file__).resolve().parent / "fixtures/retrieval-hybrid-expanded-v1.json"
    )
    source = json.loads(path.read_text(encoding="utf-8"))
    validate_dataset(source)
    assert len(source["documents"]) == 12
    assert len(source["cases"]) == 96
    assert sum(bool(case["expected"]) for case in source["cases"]) == 48
    source["cases"][0]["evidence"] = ["invented unsupported evidence"]
    with pytest.raises(ValueError, match="evidence"):
        validate_dataset(source)


def test_output_cannot_destroy_labels_or_unresolved_cleanup_journal(tmp_path):
    dataset, output = tmp_path / "labels.json", tmp_path / "result.json"
    dataset.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="dataset"):
        validate_output(dataset, dataset)
    output.write_text('{"cleanup":"pending"}', encoding="utf-8")
    with pytest.raises(ValueError, match="cleanup"):
        validate_output(dataset, output)
    output.write_text('{"cleanup":"completed"}', encoding="utf-8")
    validate_output(dataset, output)


@pytest.mark.asyncio
async def test_session_refreshes_before_measurement_with_csrf():
    now, calls = [0], []

    class Client:
        async def get(self, path, **kwargs):
            calls.append(path)
            return SimpleNamespace(
                status_code=200, json=lambda: {"csrf_token": "synthetic-csrf"}
            )

        async def post(self, path, *, headers):
            calls.append(path)
            assert headers["X-CSRF-Token"] == "synthetic-csrf"
            assert headers["Origin"] == "http://test"
            return SimpleNamespace(
                status_code=200, json=lambda: {"access_token": "renewed"}
            )

    session = EvaluationSession(
        Client(), "initial", "http://test", 600, clock=lambda: now[0]
    )
    assert await session.access_token() == "initial"
    assert not calls
    now[0] = 540
    assert await session.access_token() == "renewed"
    assert calls == ["/api/v1/auth/csrf", "/api/v1/auth/refresh"]
    assert session.refreshes == 1
    assert await session.access_token() == "renewed"
    assert session.refreshes == 1


@pytest.mark.asyncio
async def test_failed_refresh_stops_measurements_instead_of_hiding_auth_errors():
    class Client:
        async def get(self, *args, **kwargs):
            return SimpleNamespace(status_code=401)

    session = EvaluationSession(Client(), "expired", "http://test", 1, clock=lambda: 0)
    with pytest.raises(RuntimeError, match="CSRF"):
        await session.access_token()


def test_atomic_output_does_not_overwrite_predictable_temporary_file(tmp_path):
    output = tmp_path / "result.json"
    labels = tmp_path / "result.json.tmp"
    labels.write_text("preserved labels", encoding="utf-8")
    save(output, {"cleanup": "pending"})
    assert labels.read_text(encoding="utf-8") == "preserved labels"
    assert json.loads(output.read_text(encoding="utf-8")) == {"cleanup": "pending"}
    assert set(tmp_path.iterdir()) == {output, labels}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_provision_waits_for_write_before_cleanup(cancellations):
    started, release = threading.Event(), threading.Event()
    order = []

    class Index:
        def upsert(self, **kwargs):
            assert kwargs["namespace"] == "synthetic-test"
            started.set()
            assert release.wait(5)
            order.append("write")

    async def run():
        try:
            await provision_vector(Index(), namespace="synthetic-test")
        finally:
            order.append("cleanup")

    task = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert not order
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert order == ["write", "cleanup"]


@pytest.mark.asyncio
async def test_concurrent_runs_cannot_share_a_cleanup_journal(monkeypatch, tmp_path):
    from scripts import hybrid_expanded_benchmark as benchmark

    started, release = asyncio.Event(), asyncio.Event()
    output = tmp_path / "result.json"

    async def run(*args):
        started.set()
        await release.wait()

    monkeypatch.setattr(benchmark, "_compare", run)
    task = asyncio.create_task(benchmark.compare(tmp_path / "labels.json", output, 1))
    await started.wait()
    try:
        with pytest.raises(FileExistsError):
            await benchmark.compare(tmp_path / "labels.json", output, 1)
    finally:
        release.set()
        await task
    assert not output.with_suffix(".json.lock").exists()
