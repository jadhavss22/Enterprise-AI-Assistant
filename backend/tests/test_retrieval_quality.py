# ruff: noqa: F811
import asyncio
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi import HTTPException
from test_admin_users import bearer, create, env, login  # noqa: F401
from test_chat_integration import events
from test_chat_integration import setup as setup_chat
from test_retrieval import match

from app.chat import service as chat_service
from app.config import settings
from app.retrieval import quality, reranker, service
from scripts import retrieval_quality_experiment


@pytest.mark.parametrize(
    "original, rewritten, valid",
    [
        ("Where is HR-42?", "Find the HR-42 policy.", True),
        ("Where is HR-42?", "Find the HR-420 policy.", False),
        ("What is the 42 day limit?", "Explain the 142 day limit.", False),
        ('Find "annual leave" rules', "Explain annual leave eligibility.", True),
        ('Find "annual leave" rules', "Explain vacation eligibility.", False),
        ("Can I apply without approval?", "Can I apply with approval?", False),
        ("Where is HR-42?", "Where is HR-42?", False),
        ("What is the leave limit?", "What is the 30 day leave limit?", False),
        ("Where is HR-42?", "Compare HR-42 and HR-43.", False),
        ("Can I apply?", "Can I apply without approval?", False),
        ("Don't include contractors", "Include contractors", False),
        ("I can’t apply", "I can apply", False),
        ("I cannot apply", "I can apply", False),
        ("I cannot apply", "Why can I not apply?", True),
        ("Question", "two\nlines", False),
        ("Question", "", False),
        ("Question", None, False),
    ],
)
def test_rewrite_preserves_literal_constraints(original, rewritten, valid):
    assert (quality.valid_rewrite(original, rewritten) is not None) is valid


@pytest.mark.asyncio
async def test_rewrite_bounds_and_provider_fallback(monkeypatch):
    class Provider:
        async def complete(self, messages, **kwargs):
            assert kwargs["max_tokens"] == 256
            assert "untrusted" in messages[0]["content"]
            return "What are the annual leave rules?"

    assert await quality.rewrite_search_query("Explain time off", "test", Provider())

    class Slow:
        async def complete(self, *args, **kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr(settings, "retrieval_rewrite_timeout_seconds", 0.01)
    assert await quality.rewrite_search_query("question", "test", Slow()) is None


@pytest.mark.asyncio
async def test_reranking_validates_scores_and_redacts_task_arguments(monkeypatch):
    candidates = [
        {"document_id": "doc", "text": "first", "chunk_id": "a"},
        {"document_id": "doc", "text": "second", "chunk_id": "b"},
    ]
    state = {"payload": {"scores": [-1.0, 2.0]}, "forgotten": 0, "revoked": 0}

    class Result:
        def get(self, **kwargs):
            if state.get("timeout"):
                raise TimeoutError
            return state["payload"]

        def forget(self):
            state["forgotten"] += 1

        def revoke(self, **kwargs):
            assert kwargs == {"terminate": False}
            state["revoked"] += 1

    def dispatch(**kwargs):
        assert "first" not in kwargs["argsrepr"]
        assert kwargs["args"] == [
            [["question", "safe.txt: first"], ["question", "safe.txt: second"]]
        ]
        assert kwargs["retry"] is False
        return Result()

    monkeypatch.setattr(
        quality, "rerank_passages", SimpleNamespace(apply_async=dispatch)
    )
    names = {"doc": "safe.txt"}
    result = await quality.rerank_candidates("question", candidates, names, "test")
    assert [item["chunk_id"] for item in result] == ["b", "a"]
    for payload in (
        {"scores": [1]},
        {"scores": [float("nan"), 1]},
        {"scores": [True, 1]},
        {"error": "RERANK_UNAVAILABLE"},
    ):
        state["payload"] = payload
        assert (
            await quality.rerank_candidates("question", candidates, names, "test")
            == candidates
        )
    state["timeout"] = True
    assert (
        await quality.rerank_candidates("question", candidates, names, "test")
        == candidates
    )
    assert state["revoked"] == 1 and state["forgotten"] == 6


def test_worker_rejects_invalid_input_and_cannot_load_on_query(monkeypatch):
    monkeypatch.setattr(settings, "retrieval_rerank_enabled", True)
    monkeypatch.setattr(reranker, "_model", None)
    assert reranker.score_pairs([["question", "passage"]]) == {
        "error": "RERANK_UNAVAILABLE"
    }
    monkeypatch.setattr(reranker, "_model", object())
    monkeypatch.setattr(reranker, "predict", lambda model, pairs: [2.5] * len(pairs))
    assert reranker.score_pairs([["question", "passage"]]) == {"scores": [2.5]}
    for pairs in (
        None,
        [],
        [["only one"]],
        [["question", None]],
        [["q", "x" * 100001]],
    ):
        assert reranker.score_pairs(pairs) == {"error": "RERANK_INPUT_INVALID"}
    monkeypatch.setattr(reranker, "predict", lambda model, pairs: [float("inf")])
    assert reranker.score_pairs([["q", "p"]]) == {"error": "RERANK_INVALID"}


def test_model_is_pinned_and_cache_only(monkeypatch):
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(
            CrossEncoder=lambda *args, **kwargs: calls.append((args, kwargs))
        ),
    )
    reranker.load_model()
    args, options = calls[0]
    assert args == (reranker.MODEL,)
    assert options["revision"] == reranker.REVISION
    assert options["local_files_only"] is True
    assert options["trust_remote_code"] is False
    assert options["model_kwargs"] == {"use_safetensors": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["unavailable", "long", "timeout", "unauthorized"])
async def test_optional_query_retry_is_bounded_and_does_not_hide_auth_errors(
    monkeypatch, failure
):
    original = {"status": "NO_RELEVANT_MATCHES", "passages": [], "request_id": "test"}
    calls = []

    async def retrieve(db, actor, query, *args, **kwargs):
        calls.append(query)
        if query == "original":
            return original
        if failure == "timeout":
            await asyncio.sleep(1)
        status, code = {
            "unavailable": (503, "SEARCH_UNAVAILABLE"),
            "long": (422, "QUERY_TOO_LONG"),
            "unauthorized": (401, "SESSION_INVALID"),
        }[failure]
        raise HTTPException(status, detail={"code": code})

    async def actor(db, user):
        return user

    async def rewrite(query, request_id):
        return "rewritten"

    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", True)
    monkeypatch.setattr(settings, "retrieval_query_retry_timeout_seconds", 0.01)
    monkeypatch.setattr(service, "_retrieve", retrieve)
    monkeypatch.setattr(service, "revalidate_actor", actor)
    monkeypatch.setattr(quality, "rewrite_search_query", rewrite)
    if failure == "unauthorized":
        with pytest.raises(HTTPException) as error:
            await service.retrieve(None, {}, "original", "test")
        assert error.value.status_code == 401
    else:
        assert await service.retrieve(None, {}, "original", "test") == original
    assert calls == ["original", "rewritten"]


@pytest.mark.asyncio
async def test_evaluation_detects_silent_fallbacks(monkeypatch, tmp_path):
    async def live(dataset):
        print(json.dumps({"cases": [], "dataset_version": 3}))

    monkeypatch.setattr(retrieval_quality_experiment, "live_check", live)
    output = tmp_path / "result.json"
    with pytest.raises(SystemExit):
        await retrieval_quality_experiment.compare("synthetic.json", output)
    result = json.loads(output.read_text())
    assert "error" not in result["profiles"]["baseline"]
    assert result["profiles"]["reranking"]["error"] == "RerankerNotExercised"
    assert result["profiles"]["rewriting"]["error"] == "RewriterNotExercised"


@pytest.mark.asyncio
async def test_quality_route_rechecks_permissions_versions_and_retry_errors(
    env, monkeypatch
):
    db, client = env["db"], env["client"]
    await create(env, "quality@example.com", "EMPLOYEE")
    auth, _ = await login(env, "quality@example.com")
    employee_id = ObjectId(auth["user"]["user_id"])
    doc = {
        "_id": ObjectId(),
        "filename": "synthetic.txt",
        "department_id": ObjectId(env["department_id"]),
        "uploaded_by": ObjectId(env["admin_id"]),
        "visibility": "PRIVATE",
        "allowed_user_ids": [employee_id],
        "status": "READY",
        "active_index_version": 1,
        "chunk_count": 2,
    }
    await db.documents.insert_one(doc)
    queries = []

    async def embedding(query):
        queries.append(query)
        return [0.1]

    async def search(docs, vector):
        if queries[-1] == "initial":
            return [], 1, None
        return (
            [
                (item, match(item, score=0.9 - i / 10, chunk_index=i))
                for item in docs
                for i in range(2)
            ],
            1,
            None,
        )

    async def rewrite(query, request_id):
        return "rewritten"

    async def ranking(query, candidates, filenames, request_id):
        return list(reversed(candidates))

    monkeypatch.setattr(settings, "retrieval_rate_limit", 100)
    monkeypatch.setattr(service, "query_embedding", embedding)
    monkeypatch.setattr(service, "search_namespaces", search)
    monkeypatch.setattr(quality, "rewrite_search_query", rewrite)
    monkeypatch.setattr(quality, "rerank_candidates", ranking)
    headers = bearer(auth)

    async def request(query="initial"):
        return await client.post(
            "/api/v1/retrieval/search", headers=headers, json={"query": query}
        )

    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", False)
    assert (await request()).json()["passages"] == []
    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", True)
    monkeypatch.setattr(settings, "retrieval_rerank_enabled", True)
    response = await request()
    assert response.status_code == 200, response.text
    assert [p["chunk_index"] for p in response.json()["passages"]] == [1, 0]
    assert queries[-2:] == ["initial", "rewritten"]
    assert "scores" not in response.text
    queries.clear()
    assert (await request("direct")).status_code == 200
    assert queries == ["direct"], "Existing matches must bypass the rewrite provider"

    async def revoke(query, candidates, filenames, request_id):
        await db.documents.update_one(
            {"_id": doc["_id"]}, {"$set": {"allowed_user_ids": []}}
        )
        return candidates

    monkeypatch.setattr(quality, "rerank_candidates", revoke)
    assert (await request("direct")).json()["passages"] == []
    await db.documents.update_one(
        {"_id": doc["_id"]}, {"$set": {"allowed_user_ids": [employee_id]}}
    )

    async def reindex(query, candidates, filenames, request_id):
        await db.documents.update_one(
            {"_id": doc["_id"]}, {"$set": {"active_index_version": 2}}
        )
        return candidates

    monkeypatch.setattr(quality, "rerank_candidates", reindex)
    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", False)
    assert (await request("direct")).json()["passages"] == []
    await db.documents.update_one(
        {"_id": doc["_id"]}, {"$set": {"active_index_version": 1}}
    )
    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", True)
    response = await request("direct")
    assert response.status_code == 200, response.text
    assert response.json()["passages"]
    assert {p["index_version"] for p in response.json()["passages"]} == {2}

    async def disable(query, request_id):
        await db.users.update_one(
            {"_id": employee_id}, {"$set": {"status": "DISABLED"}}
        )
        return "rewritten"

    monkeypatch.setattr(quality, "rewrite_search_query", disable)
    assert (await request()).status_code == 401


@pytest.mark.asyncio
async def test_chat_uses_shared_quality_path_without_changing_answer_question(
    env, monkeypatch
):
    provider, _doc, _, cid, headers = await setup_chat(env, monkeypatch)
    queries = []
    stream = provider.stream

    async def checked_stream(messages):
        assert (
            json.loads(messages[-1]["content"])["question"]
            == "Initial policy question?"
        )
        async for text in stream(messages):
            yield text

    monkeypatch.setattr(provider, "stream", checked_stream)

    async def embedding(query):
        queries.append(query)
        return [0.1]

    async def search(docs, vector):
        return (
            (
                [(d, match(d)) for d in docs]
                if queries[-1] == "standalone rewrite"
                else []
            ),
            1,
            None,
        )

    async def rewrite(query, request_id):
        assert query == "Initial policy question?"
        return "standalone rewrite"

    monkeypatch.setattr(settings, "retrieval_query_rewrite_enabled", True)
    monkeypatch.setattr(service, "query_embedding", embedding)
    monkeypatch.setattr(service, "search_namespaces", search)
    monkeypatch.setattr(quality, "rewrite_search_query", rewrite)
    monkeypatch.setattr(chat_service, "retrieve", service.retrieve)
    response = await env["client"].post(
        f"/api/v1/conversations/{cid}/messages/stream",
        headers=headers,
        json={
            "question": "Initial policy question?",
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert events(response)[-1][0] == "completed", response.text
    assert queries == ["Initial policy question?", "standalone rewrite"]
