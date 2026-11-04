"""Opt-in real RAG smoke, cloning ONLY named synthetic document metadata.

Run in API container. Disposable auth/chat database; live vectors are read-only.
Never prints credentials, tokens, prompts, answers or actual document contents.
"""

import asyncio
import json
import secrets
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import get_db, get_redis
from app.chat.storage import ensure_chat_storage
from app.config import settings
from app.main import app
from app.workers.celery_app import embedding_runtime_probe


async def main():
    mongo = AsyncIOMotorClient(settings.mongodb_url, tz_aware=True)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    name = "test_live_chat_" + uuid4().hex
    db = mongo[name]
    previous = app.dependency_overrides.copy()
    old_prefix = settings.auth_rate_prefix
    settings.auth_rate_prefix = name + ":"
    names = {
        "ingestion-smoke-test.txt",
        "qa-support-policy.pdf",
        "qa-training-policy.docx",
    }
    try:
        docs = (
            await mongo[settings.mongodb_database]
            .documents.find(
                {
                    "filename": {"$in": sorted(names)},
                    "deleted_at": {"$exists": False},
                    "active_index_version": {"$gt": 0},
                }
            )
            .to_list(length=10)
        )
        if {d["filename"] for d in docs} != names:
            raise RuntimeError("Required synthetic fixtures are not all active")
        probe = await asyncio.to_thread(embedding_runtime_probe.delay)
        try:
            await asyncio.to_thread(probe.get, timeout=240)
        finally:
            await asyncio.to_thread(probe.forget)
        password = secrets.token_urlsafe(32)
        await bootstrap_in_database(
            db, "synthetic-chat@example.com", "Synthetic Chat", "engineering", password
        )
        await ensure_chat_storage(db)
        user = await db.users.find_one(
            {"email_normalized": "synthetic-chat@example.com"}
        )
        for doc in docs:
            doc.update(
                uploaded_by=user["_id"],
                department_id=user["department_id"],
                visibility="PRIVATE",
                allowed_user_ids=[],
            )
        await db.documents.insert_many(docs)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_redis] = lambda: redis
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                headers={"Origin": settings.frontend_origin},
                json={"email": "synthetic-chat@example.com", "password": password},
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            created = await client.post(
                "/api/v1/conversations",
                headers=headers,
                json={"title": "Synthetic real RAG smoke"},
            )
            assert created.status_code == 201
            cid = created.json()["conversation_id"]
            results = []
            for question in [
                "How many annual leave days does Cedar Demo provide?",
                "How many days was that?",
            ]:
                response = await client.post(
                    f"/api/v1/conversations/{cid}/messages/stream",
                    headers=headers,
                    json={"question": question, "client_request_id": str(uuid4())},
                )
                parsed = [
                    (frame.splitlines()[0][7:], json.loads(frame.splitlines()[1][6:]))
                    for frame in response.text.strip().split("\n\n")
                    if frame.startswith("event: ")
                ]
                answer = "".join(
                    data.get("text", "") for event, data in parsed if event == "token"
                )
                references = [
                    c
                    for event, data in parsed
                    if event == "citations"
                    for c in data.get("citations", [])
                ]
                errors = [
                    data.get("code") for event, data in parsed if event == "error"
                ]
                success = bool(
                    parsed
                    and parsed[-1][0] == "completed"
                    and "20" in answer
                    and references
                    and all(c["filename"] in names for c in references)
                )
                results.append(
                    {
                        "http_status": response.status_code,
                        "completed": success,
                        "source_count": len(references),
                        "error_codes": errors,
                    }
                )
                if not success:
                    break
            print(
                json.dumps(
                    {
                        "provider": settings.chat_provider,
                        "model": settings.gemini_chat_model,
                        "checks": results,
                    }
                )
            )
            if len(results) != 2 or not all(r["completed"] for r in results):
                return 1
            messages = await client.get(
                f"/api/v1/conversations/{cid}/messages", headers=headers
            )
            assert len(messages.json()["turns"]) == 2 and all(
                t["is_complete"] for t in messages.json()["turns"]
            )
            assert (
                await client.delete(f"/api/v1/conversations/{cid}", headers=headers)
            ).status_code == 204
        return 0
    finally:
        app.dependency_overrides = previous
        settings.auth_rate_prefix = old_prefix
        keys = [k async for k in redis.scan_iter(match=name + ":*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        assert name.startswith("test_live_chat_")
        await mongo.drop_database(name)
        mongo.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
