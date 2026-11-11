# ruff: noqa: F811
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import OperationFailure
from test_admin_users import bearer, create, env, login  # noqa: F401

from app import admin_dashboard


@pytest.mark.asyncio
async def test_dashboard_counts_windows_roles_and_degradation(env, monkeypatch):
    db, client = env["db"], env["client"]
    headers = bearer(env["admin"])
    path = "/api/v1/admin/dashboard"
    now = datetime(2026, 11, 5, tzinfo=UTC)
    monkeypatch.setattr(admin_dashboard, "utcnow", lambda: now)
    week = now - timedelta(days=7)
    older = week - timedelta(milliseconds=1)
    month = now - timedelta(days=30)
    await db.documents.insert_many([
        {"status": "READY"}, {"status": "PROCESSING"},
        {"status": "READY", "deleted_at": now},
    ])
    await db.ingestion_jobs.insert_many([
        {"status": "FAILED", "updated_at": week},
        {"status": "FAILED", "updated_at": month},
        {"status": "FAILED", "updated_at": now},
        {"status": "QUEUED", "updated_at": week},
    ])
    await db.conversations.insert_many([
        {"created_at": week}, {"created_at": week, "deleted_at": now},
        {"created_at": older},
    ])
    await db.chat_turns.insert_many([
        {"created_at": week, "status": "COMPLETED", "answer": "private-answer"},
        {"created_at": week, "status": "INTERRUPTED"},
        {"created_at": older, "status": "COMPLETED"},
    ])
    vote = await db.feedback.insert_one({
        "rating": "THUMBS_UP", "created_at": week, "comment": "private-comment"
    })
    await db.feedback.insert_one({
        "rating": "THUMBS_DOWN", "created_at": older, "updated_at": week
    })
    for role in ("EMPLOYEE", "MANAGER"):
        user = await create(env, f"dashboard-{role.lower()}@example.com", role=role)
        auth = await login(env, user["email"])
        assert (await client.get(path, headers=bearer(auth[0]))).status_code == 403
    await db.users.insert_one({"status": "DISABLED", "role": "EMPLOYEE"})

    async def warming(_redis):
        return {"status": "warming"}

    monkeypatch.setattr(admin_dashboard, "model_readiness", warming)
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["days"] == 7
    assert data["documents"] == {"total": 2, "by_status": {"READY": 1, "PROCESSING": 1}}
    assert data["failed_jobs"] == 1
    assert data["users"] == {
        "active_by_role": {"EMPLOYEE": 1, "MANAGER": 1, "ADMIN": 1}, "disabled": 1,
    }
    assert data["chat"] == {"conversations": 1, "questions": 2, "completed": 1}
    assert data["feedback"] == {"THUMBS_UP": 1, "THUMBS_DOWN": 0}
    assert data["health"] == {"api": "healthy", "worker": "starting"}
    assert "private-" not in response.text
    assert (await client.get(path + "?days=14", headers=headers)).status_code == 422

    await db.feedback.update_one({"_id": vote.inserted_id}, {"$set": {"rating": "THUMBS_DOWN"}})

    async def offline(_redis):
        return {"status": "unavailable"}

    async def broken(_db):
        raise OperationFailure("internal error")

    monkeypatch.setattr(admin_dashboard, "model_readiness", offline)
    monkeypatch.setattr(admin_dashboard, "documents", broken)
    response = await client.get(path + "?days=30", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["documents"] is None
    assert data["failed_jobs"] == 2
    assert data["chat"] == {"conversations": 2, "questions": 3, "completed": 2}
    assert data["feedback"] == {"THUMBS_UP": 0, "THUMBS_DOWN": 2}
    assert data["health"] == {"api": "healthy", "worker": "unavailable"}
