from threading import Event

import pytest
from bson import ObjectId
from fastapi import HTTPException
from test_retrieval import match

from app.config import settings
from app.retrieval import lexical, pool


def test_keyword_terms_and_fusion():
    assert (
        lexical.lexical_score(
            lexical.phrases("Where is policy HR-42?"), "The current HR-42 applies."
        )
        == 1
    )
    query = lexical.phrases("What are the support hours?")
    assert lexical.lexical_score(query, "Our SUPPORT hours are listed below.") == 2
    assert (
        lexical.lexical_score(query, "Support cases are closed after two hours.") == 0
    )
    semantic = [{"chunk_id": "a"}, {"chunk_id": "b"}]
    keyword = [{"chunk_id": "c"}, {"chunk_id": "b"}]
    assert [c["chunk_id"] for c in lexical.fuse(semantic, keyword)] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_fetch_requests_only_active_ids_and_respects_total_budget():
    doc = {"_id": ObjectId(), "active_index_version": 3, "chunk_count": 2}
    rows = [match(doc, chunk_index=i) for i in range(2)]
    calls = []

    class Index:
        def fetch(self, **kwargs):
            calls.append(kwargs)
            return {"vectors": {row["id"]: row for row in rows} | {"unexpected": {}}}

    index = Index()
    result = await lexical.fetch_active_chunks([doc], index)
    assert result == [(doc, row) for row in rows]
    assert calls[0]["namespace"] == f"{doc['_id']}-v3"
    assert calls[0]["ids"] == [row["id"] for row in rows]
    assert 0 < calls[0]["timeout"] <= 4
    assert not await lexical.fetch_active_chunks(
        [doc] * (lexical.MAX_CHUNKS // 2 + 1), index
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_keyword_timeout_does_not_consume_semantic_capacity(monkeypatch):
    optional = pool.ProviderPool(1)
    required = pool.ProviderPool(1)
    started, release = Event(), Event()
    doc = {"_id": ObjectId(), "active_index_version": 1, "chunk_count": 1}

    class SlowIndex:
        def fetch(self, **kwargs):
            started.set()
            release.wait(timeout=5)
            return {"vectors": {}}

    monkeypatch.setattr(lexical, "keyword_pool", optional)
    monkeypatch.setattr(pool, "provider_pool", required)
    monkeypatch.setattr(settings, "retrieval_search_timeout_seconds", 0.2)
    try:
        with pytest.raises(TimeoutError):
            await lexical.fetch_active_chunks([doc], SlowIndex())
        assert started.is_set()
        assert await pool.blocking_call(lambda: "semantic-ready") == "semantic-ready"
        with pytest.raises(HTTPException) as error:
            await optional.call(lambda: None)
        assert error.value.detail["code"] == "PROVIDER_BUSY"
    finally:
        release.set()
        optional.close()
        required.close()
