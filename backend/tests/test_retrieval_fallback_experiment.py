"""Check experiment gating without downloading or loading a model."""

import sys
from types import SimpleNamespace

import pytest

from app.retrieval import routes, service
from scripts import retrieval_rerank_experiment as experiment


@pytest.mark.asyncio
async def test_fallback_only_scores_candidates_below_baseline(monkeypatch):
    calls = []
    original_retrieve = routes.retrieve
    baseline = {"document_id": "a", "chunk_id": "a", "score": 0.25, "text": "A"}
    broader = {"document_id": "b", "chunk_id": "b", "score": 0.22, "text": "B"}
    filenames = {"a": "a.txt", "b": "b.txt"}

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def predict(self, pairs, **kwargs):
            calls.extend(pairs)
            return [-3.0]

    async def verify(_dataset):
        if routes.retrieve is original_retrieve:
            assert experiment.settings.retrieval_min_score == 0.25
            return
        rerank = routes.retrieve.keywords["rerank"]
        assert await rerank("question", [baseline, broader], filenames) == [baseline]
        assert calls == []
        assert await rerank("question", [broader], filenames) == [broader]
        assert calls == [("question", "b.txt: B")]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=Model))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(nn=SimpleNamespace(Identity=object)))
    monkeypatch.setattr(experiment, "main", verify)
    await experiment.compare("unused", -4.0, fallback=True)
    assert calls == [("question", "b.txt: B")]
    assert routes.retrieve is service.retrieve
