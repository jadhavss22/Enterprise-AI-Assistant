"""Admin authorization and transaction invariants on a real replica set."""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth import admin_service
from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import get_db, get_redis
from app.auth.security import verify_password
from app.auth.tokens import decode_access_token
from app.config import settings
from app.main import app

PASSWORD = "a long test-only step C password"
ORIGIN = {"Origin": "http://localhost:3000"}


@pytest_asyncio.fixture
async def env(monkeypatch):
    mongo_url = os.environ.get("AUTH_TEST_MONGODB_URL")
    redis_url = os.environ.get("AUTH_TEST_REDIS_URL")
    if not mongo_url or not redis_url:
        pytest.skip("Set MongoDB/Redis test URLs; Admin tests require a replica set")
    mongo = AsyncIOMotorClient(
        mongo_url,
        directConnection=True,
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
    )
    redis = Redis.from_url(
        redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3
    )
    name = "test_auth_step_c_" + uuid4().hex
    prefix = name + ":"
    monkeypatch.setattr(settings, "auth_rate_prefix", prefix)
    db = mongo[name]
    connected = False
    previous = app.dependency_overrides.copy()
    try:
        hello = await mongo.admin.command("hello")
        connected = True
        assert hello.get("setName"), (
            "Start mongo-init: transactions require a replica set"
        )
        await redis.ping()
        admin_id = await bootstrap_in_database(
            db, "admin@example.com", "Admin", "hr", PASSWORD
        )
        department = await db.departments.find_one({"code": "HR"})
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_redis] = lambda: redis
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/auth/login",
                headers=ORIGIN,
                json={"email": "admin@example.com", "password": PASSWORD},
            )
            assert response.status_code == 200
            yield {
                "db": db,
                "redis": redis,
                "client": client,
                "admin": response.json(),
                "admin_id": admin_id,
                "department_id": str(department["_id"]),
            }
    finally:
        app.dependency_overrides = previous
        try:
            keys = [key async for key in redis.scan_iter(match=prefix + "*")]
            if keys:
                await redis.delete(*keys)
        finally:
            await redis.aclose()
            try:
                if connected:
                    assert name.startswith("test_auth_step_c_")
                    await mongo.drop_database(name)
            finally:
                mongo.close()


def bearer(data):
    return {"Authorization": "Bearer " + data["access_token"]}


def payload(env, email="new@example.com", role="EMPLOYEE"):
    return {
        "email": email,
        "display_name": "New User",
        "password": PASSWORD,
        "role": role,
        "department_id": env["department_id"],
    }


async def create(env, email="new@example.com", role="EMPLOYEE"):
    response = await env["client"].post(
        "/api/v1/admin/users",
        headers=bearer(env["admin"]),
        json=payload(env, email, role),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login(env, email):
    response = await env["client"].post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200
    return response.json(), response.cookies.get(settings.refresh_cookie_name)


@pytest.mark.asyncio
async def test_creation_normalization_hash_and_privacy(env):
    response = await env["client"].post(
        "/api/v1/admin/users",
        headers=bearer(env["admin"]),
        json=payload(env, " NEW@example.com "),
    )
    assert response.status_code == 201
    result = response.json()
    assert result["email"] == "new@example.com" and result["role"] == "employee"
    stored = await env["db"].users.find_one({"_id": ObjectId(result["user_id"])})
    assert stored["created_by"] == ObjectId(env["admin_id"])
    assert verify_password(stored["password_hash"], PASSWORD)
    assert stored["department_id"] == ObjectId(env["department_id"])
    assert "password" not in response.text and "token_version" not in result
    event = await env["db"].audit_logs.find_one(
        {"event": "admin.user_created", "outcome": "SUCCESS"}
    )
    assert event["resource_id"] == stored["_id"]
    assert event["request_id"] == response.headers["x-request-id"]
    assert PASSWORD not in str(event) and stored["password_hash"] not in str(event)


@pytest.mark.asyncio
async def test_duplicate_email_concurrency_returns_one_created_one_conflict(env):
    responses = await asyncio.gather(
        *[
            env["client"].post(
                "/api/v1/admin/users", headers=bearer(env["admin"]), json=payload(env)
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert all(not isinstance(item, BaseException) for item in responses)
    assert sorted(item.status_code for item in responses) == [201, 409]
    assert (
        await env["db"].users.count_documents({"email_normalized": "new@example.com"})
        == 1
    )
    assert (
        await env["db"].audit_logs.count_documents(
            {"event": "admin.user_created", "outcome": "SUCCESS"}
        )
        == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"password": "short"},
        {"password": "x" * 129},
        {"email": "bad"},
        {"display_name": " "},
        {"role": "SUPERADMIN"},
        {"department_id": "invalid"},
        {"created_by": "spoofed"},
        {"token_version": 100},
    ],
)
async def test_creation_rejects_invalid_and_mass_assignment(env, change):
    response = await env["client"].post(
        "/api/v1/admin/users", headers=bearer(env["admin"]), json=payload(env) | change
    )
    assert response.status_code == 422
    assert await env["db"].users.count_documents({}) == 1
    assert "spoofed" not in response.text


@pytest.mark.asyncio
async def test_creation_requires_existing_active_department(env):
    for department_id in (str(ObjectId()), env["department_id"]):
        if department_id == env["department_id"]:
            await env["db"].departments.update_one(
                {"_id": ObjectId(department_id)}, {"$set": {"status": "DISABLED"}}
            )
        response = await env["client"].post(
            "/api/v1/admin/users",
            headers=bearer(env["admin"]),
            json=payload(env) | {"department_id": department_id},
        )
        assert response.status_code == 422
    assert await env["db"].users.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["EMPLOYEE", "MANAGER"])
async def test_non_admin_cannot_read_or_mutate_users(env, role):
    user = await create(env, role=role)
    data, _ = await login(env, user["email"])
    client = env["client"]
    assert (
        await client.get("/api/v1/admin/users", headers=bearer(data))
    ).status_code == 403
    assert (
        await client.get("/api/v1/admin/departments", headers=bearer(data))
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/admin/users",
            headers=bearer(data),
            json=payload(env, "other@example.com"),
        )
    ).status_code == 403
    assert (
        await client.patch(
            "/api/v1/admin/users/" + user["user_id"],
            headers=bearer(data),
            json={"role": "ADMIN"},
        )
    ).status_code == 403
    assert (await env["db"].users.find_one({"_id": ObjectId(user["user_id"])}))[
        "role"
    ] == role


@pytest.mark.asyncio
async def test_cookie_alone_does_not_authorize_admin_api(env):
    # Fixture client has a valid refresh cookie, but lacks explicit bearer auth.
    assert (await env["client"].get("/api/v1/admin/users")).status_code == 401
    assert (
        await env["client"].post("/api/v1/admin/users", json=payload(env))
    ).status_code == 401


@pytest.mark.asyncio
async def test_pagination_is_stable_for_equal_timestamps_and_filters(env):
    for number in range(5):
        await create(
            env, f"user{number}@example.com", "EMPLOYEE" if number % 2 else "MANAGER"
        )
    # Exercise the ObjectId tie breaker, not only different timestamp values.
    first = await env["db"].users.find_one({})
    await env["db"].users.update_many({}, {"$set": {"created_at": first["created_at"]}})
    ids, cursor = [], None
    while True:
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await env["client"].get(
            "/api/v1/admin/users", headers=bearer(env["admin"]), params=params
        )
        assert response.status_code == 200 and "password_hash" not in response.text
        data = response.json()
        ids.extend(row["user_id"] for row in data["users"])
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert len(ids) == len(set(ids)) == 6
    response = await env["client"].get(
        "/api/v1/admin/users",
        headers=bearer(env["admin"]),
        params={
            "role": "MANAGER",
            "status": "ACTIVE",
            "department_id": env["department_id"],
        },
    )
    assert len(response.json()["users"]) == 3
    assert (
        await env["db"].audit_logs.count_documents({"event": "admin.users_listed"}) == 4
    )


@pytest.mark.asyncio
async def test_cursor_tampering_filter_change_and_bounds_are_rejected(env):
    await create(env)
    page = await env["client"].get(
        "/api/v1/admin/users", headers=bearer(env["admin"]), params={"limit": 1}
    )
    cursor = page.json()["next_cursor"]
    assert cursor
    for params in (
        {"cursor": cursor + "x"},
        {"cursor": cursor, "role": "ADMIN"},
        {"limit": 0},
        {"limit": 101},
        {"department_id": "bad"},
    ):
        response = await env["client"].get(
            "/api/v1/admin/users", headers=bearer(env["admin"]), params=params
        )
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", [{"status": "DISABLED"}, {"role": "MANAGER"}, {"department_id": "other"}]
)
async def test_sensitive_changes_revoke_all_target_sessions(env, change):
    user = await create(env)
    first, _ = await login(env, user["email"])
    second, raw = await login(env, user["email"])
    if "department_id" in change:
        department = await env["db"].departments.find_one({"code": "FINANCE"})
        change = {"department_id": str(department["_id"])}
    response = await env["client"].patch(
        "/api/v1/admin/users/" + user["user_id"],
        headers=bearer(env["admin"]),
        json=change,
    )
    assert response.status_code == 200
    stored = await env["db"].users.find_one({"_id": ObjectId(user["user_id"])})
    assert stored["token_version"] == 1
    for data in (first, second):
        assert (
            await env["client"].get("/api/v1/users/me", headers=bearer(data))
        ).status_code == 401
    refresh = await env["client"].post(
        "/api/v1/auth/refresh",
        headers=ORIGIN
        | {
            "Cookie": f"{settings.refresh_cookie_name}={raw}",
            "X-CSRF-Token": second["csrf_token"],
        },
    )
    assert refresh.status_code == 401
    assert (
        await env["db"].auth_sessions.count_documents(
            {"user_id": ObjectId(user["user_id"]), "revoked_at": None}
        )
        == 0
    )


@pytest.mark.asyncio
async def test_reenable_does_not_restore_old_tokens(env):
    user = await create(env)
    old, _ = await login(env, user["email"])
    path = "/api/v1/admin/users/" + user["user_id"]
    for status in ("DISABLED", "ACTIVE"):
        assert (
            await env["client"].patch(
                path, headers=bearer(env["admin"]), json={"status": status}
            )
        ).status_code == 200
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(old))
    ).status_code == 401
    fresh, _ = await login(env, user["email"])
    assert decode_access_token(fresh["access_token"])["tv"] == 2


@pytest.mark.asyncio
async def test_name_only_and_noop_updates_keep_sessions(env):
    user = await create(env)
    data, _ = await login(env, user["email"])
    for change in (
        {"display_name": " Renamed User "},
        {"role": "EMPLOYEE", "status": "ACTIVE"},
    ):
        response = await env["client"].patch(
            "/api/v1/admin/users/" + user["user_id"],
            headers=bearer(env["admin"]),
            json=change,
        )
        assert response.status_code == 200
    assert (await env["db"].users.find_one({"_id": ObjectId(user["user_id"])}))[
        "token_version"
    ] == 0
    response = await env["client"].get("/api/v1/users/me", headers=bearer(data))
    assert (
        response.status_code == 200
        and response.json()["display_name"] == "Renamed User"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {},
        {"role": None},
        {"status": "UNKNOWN"},
        {"password": PASSWORD},
        {"email": "changed@example.com"},
        {"token_version": 999},
    ],
)
async def test_update_rejects_unsupported_or_null_fields(env, change):
    response = await env["client"].patch(
        "/api/v1/admin/users/" + env["admin_id"],
        headers=bearer(env["admin"]),
        json=change,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_user_and_inactive_department_are_rejected(env):
    client, headers = env["client"], bearer(env["admin"])
    assert (
        await client.patch(
            "/api/v1/admin/users/bad", headers=headers, json={"display_name": "Name"}
        )
    ).status_code == 422
    assert (
        await client.patch(
            "/api/v1/admin/users/" + str(ObjectId()),
            headers=headers,
            json={"display_name": "Name"},
        )
    ).status_code == 404
    department = await env["db"].departments.find_one({"code": "FINANCE"})
    await env["db"].departments.update_one(
        {"_id": department["_id"]}, {"$set": {"status": "DISABLED"}}
    )
    response = await client.patch(
        "/api/v1/admin/users/" + env["admin_id"],
        headers=headers,
        json={"department_id": str(department["_id"])},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"status": "DISABLED"}, {"role": "EMPLOYEE"}])
async def test_last_active_admin_cannot_be_removed(env, change):
    response = await env["client"].patch(
        "/api/v1/admin/users/" + env["admin_id"],
        headers=bearer(env["admin"]),
        json=change,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_ACTIVE_ADMIN"
    assert (
        await env["db"].users.count_documents({"role": "ADMIN", "status": "ACTIVE"})
        == 1
    )
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(env["admin"]))
    ).status_code == 200


@pytest.mark.asyncio
async def test_two_admins_concurrently_demote_themselves_leaves_one(env):
    other = await create(env, "other-admin@example.com", "ADMIN")
    other_auth, _ = await login(env, other["email"])
    results = await asyncio.gather(
        env["client"].patch(
            "/api/v1/admin/users/" + env["admin_id"],
            headers=bearer(env["admin"]),
            json={"role": "EMPLOYEE"},
        ),
        env["client"].patch(
            "/api/v1/admin/users/" + other["user_id"],
            headers=bearer(other_auth),
            json={"role": "EMPLOYEE"},
        ),
        return_exceptions=True,
    )
    assert all(not isinstance(item, BaseException) for item in results)
    assert sorted(item.status_code for item in results) == [200, 409]
    assert (
        await env["db"].users.count_documents({"role": "ADMIN", "status": "ACTIVE"})
        == 1
    )
    assert (
        await env["db"].audit_logs.count_documents(
            {"event": "admin.user_updated", "outcome": "SUCCESS"}
        )
        == 1
    )
    assert (
        await env["db"].audit_logs.count_documents(
            {"metadata.reason": "LAST_ACTIVE_ADMIN", "outcome": "DENIED"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_mutual_disables_revalidate_acting_admin(env):
    other = await create(env, "other-admin@example.com", "ADMIN")
    other_auth, _ = await login(env, other["email"])
    results = await asyncio.gather(
        env["client"].patch(
            "/api/v1/admin/users/" + other["user_id"],
            headers=bearer(env["admin"]),
            json={"status": "DISABLED"},
        ),
        env["client"].patch(
            "/api/v1/admin/users/" + env["admin_id"],
            headers=bearer(other_auth),
            json={"status": "DISABLED"},
        ),
        return_exceptions=True,
    )
    assert all(not isinstance(item, BaseException) for item in results)
    assert sum(item.status_code == 200 for item in results) == 1
    assert all(item.status_code in {200, 401, 403} for item in results)
    assert (
        await env["db"].users.count_documents({"role": "ADMIN", "status": "ACTIVE"})
        == 1
    )


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_user_and_session_changes(env, monkeypatch):
    user = await create(env)
    data, _ = await login(env, user["email"])

    async def unavailable(*args, **kwargs):
        raise HTTPException(
            503, detail={"code": "AUDIT_UNAVAILABLE", "message": "Audit unavailable"}
        )

    monkeypatch.setattr(admin_service, "audit_admin", unavailable)
    response = await env["client"].patch(
        "/api/v1/admin/users/" + user["user_id"],
        headers=bearer(env["admin"]),
        json={"status": "DISABLED"},
    )
    assert response.status_code == 503
    stored = await env["db"].users.find_one({"_id": ObjectId(user["user_id"])})
    assert stored["status"] == "ACTIVE" and stored["token_version"] == 0
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(data))
    ).status_code == 200


@pytest.mark.asyncio
async def test_write_rate_limit_and_read_only_department_choices(env, monkeypatch):
    monkeypatch.setattr(settings, "admin_write_rate_limit", 1)
    await create(env)
    response = await env["client"].post(
        "/api/v1/admin/users",
        headers=bearer(env["admin"]),
        json=payload(env, "other@example.com"),
    )
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0
    response = await env["client"].get(
        "/api/v1/admin/departments", headers=bearer(env["admin"])
    )
    assert response.status_code == 200 and len(response.json()["departments"]) == 3
    assert "password" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "list"])
async def test_audit_failure_blocks_creation_and_user_listing(
    env, monkeypatch, operation
):
    async def unavailable(*args, **kwargs):
        raise HTTPException(
            503, detail={"code": "AUDIT_UNAVAILABLE", "message": "Audit unavailable"}
        )

    monkeypatch.setattr(admin_service, "audit_admin", unavailable)
    if operation == "create":
        response = await env["client"].post(
            "/api/v1/admin/users", headers=bearer(env["admin"]), json=payload(env)
        )
    else:
        response = await env["client"].get(
            "/api/v1/admin/users", headers=bearer(env["admin"])
        )
    assert response.status_code == 503
    assert "users" not in response.json()
    assert await env["db"].users.count_documents({}) == 1
    assert (await env["db"].auth_admin_guard.find_one({}))["revision"] == 0
