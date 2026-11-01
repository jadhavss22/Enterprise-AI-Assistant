"""Real-auth retrieval isolation, with deterministic vector/worker doubles."""

import hashlib
from datetime import UTC, datetime

import pytest
from bson import ObjectId
from test_admin_users import bearer, create, env, login  # noqa: F401

from app.config import settings
from app.retrieval import providers, service, tasks
from app.retrieval.schemas import SearchRequest


def match(doc, score=0.9, **metadata):
    chunk = metadata.get("chunk_index", 0)
    version = doc["active_index_version"]
    return {
        "id": hashlib.sha256(f"{doc['_id']}:{version}:{chunk}".encode()).hexdigest(),
        "score": score,
        "metadata": {
            "document_id": str(doc["_id"]),
            "index_version": version,
            "chunk_index": chunk,
            "text": "Synthetic support hours: Monday to Friday, 9 AM to 6 PM.",
            "page": 1,
            **metadata,
        },
    }


def test_candidate_identity_and_limits():
    doc = {"_id": ObjectId(), "active_index_version": 2, "chunk_count": 2}
    assert service.checked_candidate(doc, match(doc))
    assert service.checked_candidate(
        doc,
        match(doc, page=1.0)
        | {"metadata": match(doc)["metadata"] | {"chunk_index": 0.0, "page": 1.0}},
    )
    for item in [
        match(doc, document_id=str(ObjectId())),
        match(doc, index_version=1),
        match(doc, chunk_index=3),
        match(doc, score=float("nan")),
        match(doc, score=-1),
        match(doc, text=""),
        match(doc, page=-1),
        match(doc) | {"id": "spoofed"},
    ]:
        assert service.checked_candidate(doc, item) is None
    for text in ["", "   ", "x" * 2001]:
        with pytest.raises(ValueError):
            SearchRequest(query=text)


def test_worker_rejects_tokens_without_silent_truncation(monkeypatch):
    class Provider:
        max_input_tokens = 3
        dimension = 2

        def tokenize(self, text):
            return text.split()

        def embed(self, texts):
            return [[0.1, 0.2]]

    monkeypatch.setattr(tasks, "embedding_provider", Provider)
    assert tasks.embed_query.run("one two three four") == {"error": "QUERY_TOO_LONG"}
    assert tasks.embed_query.run("one two")["vector"] == [0.1, 0.2]


@pytest.mark.asyncio
async def test_namespace_requests_are_scoped_and_any_failure_fails_search(monkeypatch):
    docs = [{"_id": ObjectId(), "active_index_version": 2} for _ in range(2)]
    calls = []

    class Index:
        def query(self, **kwargs):
            calls.append(kwargs)
            return {"matches": []}

    monkeypatch.setattr(providers, "retrieval_index", Index)
    matches, _ = await providers.search_namespaces(docs, [0.1] * 384)
    assert matches == [] and len(calls) == 2
    for call in calls:
        doc = next(d for d in docs if call["namespace"] == f"{d['_id']}-v2")
        assert call["namespace"] == f"{doc['_id']}-v2"
        assert call["filter"]["document_id"]["$eq"] == str(doc["_id"])
        assert call["filter"]["index_version"]["$eq"] == 2
        assert call["include_metadata"] and not call["include_values"]


@pytest.mark.asyncio
async def test_search_api_enforces_current_acl_versions_and_session(env, monkeypatch):  # noqa: F811
    db, client = env["db"], env["client"]
    await create(env, "search@example.com", "EMPLOYEE")
    employee, _ = await login(env, "search@example.com")
    employee_id = ObjectId(employee["user"]["user_id"])
    department = ObjectId(env["department_id"])
    now = datetime.now(UTC)

    def document(visibility="DEPARTMENT", **extra):
        return {
            "_id": ObjectId(),
            "filename": "synthetic.txt",
            "department_id": department,
            "uploaded_by": ObjectId(env["admin_id"]),
            "visibility": visibility,
            "allowed_user_ids": [],
            "status": "READY",
            "active_index_version": 1,
            "chunk_count": 1,
            "created_at": now,
            "updated_at": now,
            **extra,
        }

    allowed = document()
    private = document("PRIVATE")
    foreign = document(department_id=ObjectId())
    deleted = document(deleted_at=now)
    staged = document(status="PROCESSING", active_index_version=None)
    await db.documents.insert_many([allowed, private, foreign, deleted, staged])
    searched = []

    async def embedding(query):
        return [0.1] * 384

    async def search(docs, vector):
        searched[:] = docs
        return [(d, match(d)) for d in docs], 1

    monkeypatch.setattr(service, "query_embedding", embedding)
    monkeypatch.setattr(service, "search_namespaces", search)
    monkeypatch.setattr(settings, "retrieval_rate_limit", 100)
    headers = bearer(employee)

    async def request(headers=headers, body=None):
        return await client.post(
            "/api/v1/retrieval/search",
            headers=headers,
            json=body or {"query": "What are the support hours?"},
        )

    assert (
        await client.post("/api/v1/retrieval/search", json={"query": "hours"})
    ).status_code == 401
    response = await request()
    assert response.status_code == 200, response.text
    assert {d["_id"] for d in searched} == {allowed["_id"]}
    passage = response.json()["passages"][0]
    assert passage["document_id"] == str(allowed["_id"]) and "score" not in passage
    assert passage["source_path"].endswith("/source")
    assert (await request(body={"query": "   "})).status_code == 422
    assert (await request(body={"query": "x" * 2001})).status_code == 422
    await db.documents.update_one(
        {"_id": allowed["_id"]}, {"$set": {"chunk_count": 10}}
    )

    async def ranked(docs, vector):
        return [
            (d, match(d, score=0.4 + i / 100, chunk_index=i))
            for d in docs
            for i in range(10)
        ] * 2, 1

    monkeypatch.setattr(service, "search_namespaces", ranked)
    ranked_response = (await request()).json()
    assert [p["chunk_index"] for p in ranked_response["passages"]] == [9, 8, 7, 6, 5]
    await db.documents.update_one({"_id": allowed["_id"]}, {"$set": {"chunk_count": 1}})
    monkeypatch.setattr(service, "search_namespaces", search)
    # Previous active version is still queryable while reprocessing.
    await db.documents.update_one(
        {"_id": allowed["_id"]}, {"$set": {"status": "PROCESSING"}}
    )
    assert (await request()).json()["status"] == "MATCHES"
    # Explicit private sharing is included, then revoked during vector search.
    await db.documents.update_one(
        {"_id": private["_id"]}, {"$set": {"allowed_user_ids": [employee_id]}}
    )
    assert len((await request()).json()["passages"]) == 2

    async def revoke(docs, vector):
        await db.documents.update_one(
            {"_id": private["_id"]}, {"$set": {"allowed_user_ids": []}}
        )
        await db.documents.update_one(
            {"_id": allowed["_id"]}, {"$set": {"active_index_version": 2}}
        )
        return [(d, match(d)) for d in docs], 1

    monkeypatch.setattr(service, "search_namespaces", revoke)
    assert (await request()).json()["passages"] == []

    async def delete(docs, vector):
        await db.documents.update_one(
            {"_id": allowed["_id"]}, {"$set": {"deleted_at": now}}
        )
        return [(d, match(d)) for d in docs], 1

    monkeypatch.setattr(service, "search_namespaces", delete)
    assert (await request()).json()["passages"] == []
    # No accessible source means no query sent to a worker/vector provider.
    response = await request()
    assert response.json()["status"] == "NO_ACCESSIBLE_DOCUMENTS"
    # Admin private reads are audited, but content/questions are not in audits.
    monkeypatch.setattr(service, "search_namespaces", search)
    response = await request(bearer(env["admin"]))
    assert response.status_code == 200
    audit = await db.audit_logs.find_one(
        {"event": "document.admin_private_access", "resource_id": private["_id"]}
    )
    assert audit and "support hours" not in str(audit)
    # Scope overflow fails visibly, never silently searches the first N docs.
    monkeypatch.setattr(settings, "retrieval_max_documents", 1)
    await db.documents.insert_one(document("ORGANIZATION"))
    response = await request(bearer(env["admin"]))
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "SEARCH_SCOPE_TOO_LARGE"
    )
    monkeypatch.setattr(settings, "retrieval_max_documents", 25)

    async def fail(docs, vector):
        raise RuntimeError("private-provider-details")

    monkeypatch.setattr(service, "search_namespaces", fail)
    response = await request()
    assert (
        response.status_code == 503 and "private-provider-details" not in response.text
    )

    async def disable(docs, vector):
        await db.users.update_one(
            {"_id": employee_id}, {"$set": {"status": "DISABLED"}}
        )
        return [(d, match(d)) for d in docs], 1

    monkeypatch.setattr(service, "search_namespaces", disable)
    assert (await request()).status_code == 401
    monkeypatch.setattr(settings, "retrieval_rate_limit", 1)
    assert (await request(bearer(env["admin"]))).status_code == 429
    # JSON body cap executes before parsing/authentication.
    response = await client.post(
        "/api/v1/retrieval/search",
        content=b"x" * 17000,
        headers={"Content-Type": "application/json", "Origin": "http://localhost:3000"},
    )
    assert (
        response.status_code == 413
        and response.headers["access-control-allow-origin"] == "http://localhost:3000"
    )


@pytest.mark.asyncio
async def test_worker_bridge_redacts_and_cleans_results(monkeypatch):
    from celery.exceptions import TimeoutError as CeleryTimeout
    from fastapi import HTTPException

    calls = []

    class Result:
        def get(self, **kwargs):
            return {"vector": [0.1] * 384, "dimension": 384}

        def revoke(self, **kwargs):
            calls.append("revoke")

        def forget(self):
            calls.append("forget")

    def publish(**kwargs):
        assert kwargs["argsrepr"] == "[<redacted query>]"
        return Result()

    monkeypatch.setattr(providers.embed_query, "apply_async", publish)
    assert len(await providers.query_embedding("synthetic question")) == 384
    assert calls == ["revoke", "forget"]
    monkeypatch.setattr(Result, "get", lambda *a, **k: {"error": "QUERY_TOO_LONG"})
    with pytest.raises(HTTPException) as error:
        await providers.query_embedding("long question")
    assert error.value.status_code == 422

    def timeout(*args, **kwargs):
        raise CeleryTimeout()

    monkeypatch.setattr(Result, "get", timeout)
    with pytest.raises(HTTPException) as error:
        await providers.query_embedding("busy worker")
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "SEARCH_BUSY"
    monkeypatch.setattr(Result, "get", lambda *a, **k: {"vector": [0.1] * 10})
    with pytest.raises(HTTPException) as error:
        await providers.query_embedding("wrong dimensions")
    assert error.value.detail["code"] == "EMBEDDING_INVALID"
