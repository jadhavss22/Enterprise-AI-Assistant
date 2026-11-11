# ruff: noqa: F811
"""Real auth + transactional Mongo/Redis; Gemini and retrieval are explicit doubles."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi import HTTPException
from test_admin_users import bearer, create, env, login  # noqa: F401

from app.auth.models import utcnow
from app.chat import service
from app.chat.grounding import ABSTENTION
from app.chat.provider import ChatProviderError
from app.chat.schemas import ChatRequest
from app.chat.storage import ensure_chat_storage
from app.config import settings


class FakeProvider:
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.invalid = False
        self.rewrites = []

    async def complete(self, messages, **kwargs):
        self.rewrites.append(json.loads(messages[-1]["content"]))
        return "How many annual leave days does Cedar provide?"

    async def stream(self, messages):
        self.calls += 1
        assert messages[0]["role"] == "system"
        if self.fail:
            yield "partial unverified"
            raise ChatProviderError("CHAT_RATE_LIMITED", "Synthetic quota", 2)
        if self.invalid:
            yield "18 days [S999]."
        else:
            yield "Cedar provides "
            yield "18 annual leave days [S1]."


def events(response):
    result = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        result.append((lines[0][7:], json.loads(lines[1][6:])))
    return result


async def actor_for(db, auth):
    from app.auth.tokens import decode_access_token

    claims = decode_access_token(auth["access_token"])
    actor = await db.users.find_one({"_id": ObjectId(claims["sub"])})
    actor["_auth_session"] = await db.auth_sessions.find_one(
        {"_id": ObjectId(claims["sid"])}
    )
    return actor


async def setup(env, monkeypatch):
    await ensure_chat_storage(env["db"])
    monkeypatch.setattr(settings, "chat_rate_limit", 30)
    provider = FakeProvider()
    monkeypatch.setattr(service, "GeminiProvider", lambda: provider)
    document = {
        "_id": ObjectId(),
        "visibility": "DEPARTMENT",
        "department_id": ObjectId(env["department_id"]),
        "uploaded_by": env["admin_id"],
        "active_index_version": 1,
        "chunk_count": 1,
        "status": "READY",
        "filename": "synthetic.txt",
    }
    await env["db"].documents.insert_one(document)
    passage = {
        "document_id": str(document["_id"]),
        "index_version": 1,
        "chunk_id": "synthetic-chunk",
        "chunk_index": 0,
        "page": None,
        "filename": "synthetic.txt",
        "source_path": f"/documents/{document['_id']}/source",
        "text": "Cedar provides 18 annual leave days.",
    }

    async def retrieve(*args):
        return {"status": "MATCHES", "passages": [passage]}

    monkeypatch.setattr(service, "retrieve", retrieve)
    client = env["client"]
    headers = bearer(env["admin"])
    response = await client.post(
        "/api/v1/conversations", headers=headers, json={"title": "Synthetic chat"}
    )
    assert response.status_code == 201, response.text
    cid = response.json()["conversation_id"]
    return provider, document, passage, cid, headers


@pytest.mark.asyncio
async def test_owner_crud_generation_history_revocation_and_idempotency(
    env, monkeypatch
):
    provider, document, _passage, cid, headers = await setup(env, monkeypatch)
    client, db = env["client"], env["db"]
    path = f"/api/v1/conversations/{cid}"
    assert (await client.get("/api/v1/conversations", headers=headers)).json()[
        "conversations"
    ][0]["conversation_id"] == cid
    employee = await create(env, "employee-chat@example.com")
    auth = await login(env, employee["email"])
    other = bearer(auth[0])
    for method, suffix, payload in [
        ("GET", "/messages", None),
        ("PATCH", "", {"title": "stolen"}),
        ("DELETE", "", None),
        (
            "POST",
            "/messages/stream",
            {"question": "Steal", "client_request_id": str(uuid4())},
        ),
        ("POST", f"/generations/{uuid4()}/cancel", None),
    ]:
        response = await client.request(
            method, path + suffix, headers=other, json=payload
        )
        assert response.status_code == 404, (method, response.text)
    assert not (await client.get("/api/v1/conversations", headers=other)).json()[
        "conversations"
    ]
    assert (
        await client.patch(path, headers=headers, json={"title": "Renamed"})
    ).json()["title"] == "Renamed"
    payload = {"question": "How many leave days?", "client_request_id": str(uuid4())}
    response = await client.post(
        path + "/messages/stream", headers=headers, json=payload
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert [e[0] for e in events(response)] == [
        "started",
        "token",
        "token",
        "citations",
        "completed",
    ]
    assert provider.calls == 1
    replay = await client.post(path + "/messages/stream", headers=headers, json=payload)
    assert events(replay)[0][1]["replayed"] is True
    assert provider.calls == 1
    conflict = await client.post(
        path + "/messages/stream",
        headers=headers,
        json=payload | {"question": "Different"},
    )
    assert conflict.status_code == 409
    rows = (await client.get(path + "/messages", headers=headers)).json()["turns"]
    assert rows[0]["is_complete"] and rows[0]["citations"][0]["document_id"] == str(
        document["_id"]
    )
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "What about those days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][0] == "completed"
    assert provider.rewrites and len(provider.rewrites[-1]["history"]) == 2
    page = (await client.get(path + "/messages?limit=1", headers=headers)).json()
    assert page["next_cursor"] and len(page["turns"]) == 1
    assert (
        len(
            (
                await client.get(
                    path + "/messages?limit=1&cursor=" + page["next_cursor"],
                    headers=headers,
                )
            ).json()["turns"]
        )
        == 1
    )
    # A version change hides both the original answer and a follow-up that used it.
    await db.documents.update_one(
        {"_id": document["_id"]}, {"$set": {"active_index_version": 2}}
    )
    rows = (await client.get(path + "/messages", headers=headers)).json()["turns"]
    assert all(
        row["status"] == "HIDDEN" and not row["answer"] and not row["citations"]
        for row in rows
    )
    replay = await client.post(path + "/messages/stream", headers=headers, json=payload)
    assert events(replay)[-1][0] == "error" and provider.calls == 2
    assert (await client.delete(path, headers=headers)).status_code == 204
    assert await db.chat_turns.count_documents({"conversation_id": ObjectId(cid)}) == 0
    assert (await client.get(path + "/messages", headers=headers)).status_code == 404
    assert (await client.get(path + "/messages")).status_code == 401


@pytest.mark.asyncio
async def test_fenced_concurrent_acquisition_cancel_expiry_and_stale_writes(
    env, monkeypatch
):
    _, _, _, cid, _ = await setup(env, monkeypatch)
    db = env["db"]
    actor = await actor_for(db, env["admin"])
    payloads = [
        ChatRequest(question="Synthetic", client_request_id=uuid4()) for _ in range(2)
    ]
    results = await asyncio.gather(
        *(service.acquire(db, cid, actor, p) for p in payloads), return_exceptions=True
    )
    assert sum(isinstance(r, tuple) for r in results) == 1
    assert (
        sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    )
    turn = next(r[0] for r in results if isinstance(r, tuple))
    await service.cancel_turn(db, cid, actor, turn["client_request_id"])
    with pytest.raises(HTTPException):
        await service.save_turn(db, cid, actor, turn, {"answer": "stale"}, final=True)
    new, _ = await service.acquire(
        db, cid, actor, ChatRequest(question="New", client_request_id=uuid4())
    )
    assert new["fence"] > turn["fence"]
    await db.conversations.update_one(
        {"_id": ObjectId(cid)},
        {"$set": {"lease_until": utcnow() - timedelta(seconds=1)}},
    )
    recovered, _ = await service.acquire(
        db, cid, actor, ChatRequest(question="Recovery", client_request_id=uuid4())
    )
    assert recovered["fence"] > new["fence"]
    assert (await db.chat_turns.find_one({"_id": new["_id"]}))[
        "status"
    ] == "INTERRUPTED"
    with pytest.raises(HTTPException):
        await service.save_turn(
            db, cid, actor, new, {"status": "COMPLETED"}, final=True
        )
    await service.mutate_conversation(db, cid, actor, delete=True)
    with pytest.raises(HTTPException):
        await service.save_turn(
            db, cid, actor, recovered, {"answer": "after delete"}, final=True
        )


@pytest.mark.asyncio
async def test_abstention_errors_and_midstream_source_revocation(env, monkeypatch):
    provider, document, _, cid, headers = await setup(env, monkeypatch)
    client, db = env["client"], env["db"]
    path = f"/api/v1/conversations/{cid}"
    provider.invalid = True
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "Days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][1]["code"] == "CHAT_CITATION_INVALID"
    assert not (await client.get(path + "/messages", headers=headers)).json()["turns"][
        0
    ]["answer"]
    provider.invalid = False
    provider.fail = True
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "Days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][1]["retry_after"] == 2
    assert not any(e[0] == "completed" for e in events(response))
    provider.fail = False

    class Revoking(FakeProvider):
        async def stream(self, messages):
            yield "partial"
            await db.documents.update_one(
                {"_id": document["_id"]}, {"$set": {"deleted_at": utcnow()}}
            )
            yield "must not be emitted [S1]"

    monkeypatch.setattr(service, "GeminiProvider", Revoking)
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "Days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][1]["code"] == "CHAT_SOURCE_CHANGED"
    assert "must not be emitted" not in response.text
    assert not any(e[0] == "citations" for e in events(response))

    async def empty(*args):
        return {"passages": []}

    monkeypatch.setattr(service, "retrieve", empty)

    class NeverCalled(FakeProvider):
        async def stream(self, messages):
            raise AssertionError("No generation with empty evidence")
            yield "unreachable"

    monkeypatch.setattr(service, "GeminiProvider", NeverCalled)
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "Unanswerable", "client_request_id": str(uuid4())},
    )
    assert ABSTENTION in response.text and events(response)[-1][0] == "completed"
    # Oversized JSON rejected before parsing; no secrets/errors are echoed.
    response = await client.post(
        path + "/messages/stream",
        headers=headers | {"Content-Type": "application/json"},
        content="x" * 17000,
    )
    assert response.status_code == 413
    audits = await db.audit_logs.find({"event": {"$regex": "^chat"}}).to_list(
        length=100
    )
    assert audits and all(
        "Days?" not in str(a) and "18 annual" not in str(a) for a in audits
    )


@pytest.mark.asyncio
async def test_employee_cannot_bypass_retrieval_and_admin_cannot_read_their_chat(
    env, monkeypatch
):
    provider, document, _, _, admin_headers = await setup(env, monkeypatch)
    client, db = env["client"], env["db"]
    employee = await create(env, "employee-rag@example.com")
    auth, _ = await login(env, employee["email"])
    headers = bearer(auth)
    response = await client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "Employee private chat"},
    )
    cid = response.json()["conversation_id"]
    path = f"/api/v1/conversations/{cid}"
    assert (
        await client.get(path + "/messages", headers=admin_headers)
    ).status_code == 404
    # Even a buggy vector/retrieval layer cannot pass unauthorized evidence to the LLM.
    await db.documents.update_one(
        {"_id": document["_id"]},
        {"$set": {"visibility": "PRIVATE", "allowed_user_ids": []}},
    )
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "Days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][1]["code"] == "CHAT_SOURCE_CHANGED"
    assert provider.calls == 0
    assert "18 annual" not in response.text


@pytest.mark.asyncio
async def test_disconnect_and_explicit_cancel_close_generation_without_final_answer(
    env, monkeypatch
):
    _, _, _, cid, _ = await setup(env, monkeypatch)
    db = env["db"]
    actor = await actor_for(db, env["admin"])

    class Blocking(FakeProvider):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.closed = False

        async def stream(self, messages):
            try:
                self.started.set()
                await asyncio.sleep(60)
                yield "late [S1]"
            finally:
                self.closed = True

    async def connected():
        return False

    for explicit in [False, True]:
        turn, replay = await service.acquire(
            db, cid, actor, ChatRequest(question="Synthetic", client_request_id=uuid4())
        )
        provider = Blocking()
        collected = []

        async def consume(
            turn=turn, replay=replay, provider=provider, collected=collected
        ):
            async for frame in service.generate(
                db,
                cid,
                actor,
                turn,
                replay,
                "synthetic-cancel",
                connected,
                provider=provider,
            ):
                collected.append(frame)

        task = asyncio.create_task(consume())
        await asyncio.wait_for(provider.started.wait(), 5)
        if explicit:
            await service.cancel_turn(db, cid, actor, turn["client_request_id"])
        else:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 8)
        assert provider.closed
        row = await db.chat_turns.find_one({"_id": turn["_id"]})
        assert row["status"] == ("CANCELLED" if explicit else "INTERRUPTED")
        assert not row["is_complete"]
        assert not any("event: completed" in f for f in collected)
        assert (await db.conversations.find_one({"_id": ObjectId(cid)}))[
            "active_request"
        ] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["version", "delete"])
async def test_history_rechecks_earlier_sources_after_page_assembly(
    env, monkeypatch, change
):
    _, document, _, cid, headers = await setup(env, monkeypatch)
    client = env["client"]
    path = f"/api/v1/conversations/{cid}/messages"
    for _ in range(2):
        response = await client.post(
            path + "/stream",
            headers=headers,
            json={"question": "Leave days?", "client_request_id": str(uuid4())},
        )
        assert response.status_code == 200
    original = service.public_turn
    calls = 0

    async def revoke_after_serialization(db, actor, turn):
        nonlocal calls
        result = await original(db, actor, turn)
        calls += 1
        if calls == 2:
            # Both individual decisions are now stale, immediately before final guard.
            assert result["is_complete"]
            update = (
                {"active_index_version": 2}
                if change == "version"
                else {"status": "DELETED"}
            )
            await db.documents.update_one({"_id": document["_id"]}, {"$set": update})
        return result

    monkeypatch.setattr(service, "public_turn", revoke_after_serialization)
    response = await client.get(path, headers=headers)
    assert response.status_code == 200
    turns = response.json()["turns"]
    assert len(turns) == 2
    assert all(
        t["status"] == "HIDDEN"
        and not t["answer"]
        and not t["citations"]
        and not t["is_complete"]
        for t in turns
    )


@pytest.mark.asyncio
async def test_feedback_owner_upsert_visibility_and_deletion(env, monkeypatch):
    provider, document, _, cid, headers = await setup(env, monkeypatch)
    client, db = env["client"], env["db"]
    path = f"/api/v1/conversations/{cid}"
    response = await client.post(
        path + "/messages/stream",
        headers=headers,
        json={"question": "How many leave days?", "client_request_id": str(uuid4())},
    )
    assert events(response)[-1][0] == "completed"
    turn = (await client.get(path + "/messages", headers=headers)).json()["turns"][0]
    feedback_path = f"/api/v1/messages/{turn['turn_id']}/feedback"
    down = {"rating": "THUMBS_DOWN", "comment": "More context, please."}
    replies = await asyncio.gather(*[
        client.post(feedback_path, headers=headers, json=down) for _ in range(2)
    ])
    assert sorted(reply.status_code for reply in replies) == [200, 201]
    assert all(reply.headers["cache-control"] == "no-store" for reply in replies)
    assert await db.feedback.count_documents({}) == 1
    turn = (await client.get(path + "/messages", headers=headers)).json()["turns"][0]
    assert turn["feedback"]["comment"] == down["comment"]

    employee = await create(env, "feedback-other@example.com")
    auth = await login(env, employee["email"])
    assert (await client.post(feedback_path, headers=bearer(auth[0]), json=down)).status_code == 404
    oversized = await client.post(
        feedback_path, headers=headers, json=down | {"comment": "x" * 17000}
    )
    assert oversized.status_code == 413
    too_long = down | {"comment": "x" * 1001}
    assert (await client.post(feedback_path, headers=headers, json=too_long)).status_code == 422
    up = await client.post(feedback_path, headers=headers, json={"rating": "THUMBS_UP"})
    assert up.status_code == 200 and up.json()["comment"] is None
    assert provider.calls == 1
    await db.documents.update_one({"_id": document["_id"]}, {"$set": {"active_index_version": 2}})
    assert (await client.post(feedback_path, headers=headers, json=down)).status_code == 409
    turn = (await client.get(path + "/messages", headers=headers)).json()["turns"][0]
    assert turn["status"] == "HIDDEN" and turn["feedback"] is None
    assert (await client.delete(path, headers=headers)).status_code == 204
    assert await db.feedback.count_documents({}) == 0
