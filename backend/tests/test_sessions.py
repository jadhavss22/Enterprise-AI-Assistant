"""Real MongoDB and Redis tests, with isolated databases and key namespaces."""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ServerSelectionTimeoutError
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.csrf import require_origin
from app.auth.dependencies import get_db, get_redis
from app.auth.models import UserRecord, utcnow
from app.auth.rate_limits import rate_limit
from app.auth.security import validate_security_settings
from app.auth.service import login, logout_all
from app.auth.tokens import decode_access_token, refresh_digest
from app.config import Settings, settings
from app.main import app

PASSWORD = "a long test-only step B password"
ORIGIN = {"Origin": "http://localhost:3000"}


@pytest_asyncio.fixture
async def env(monkeypatch):
    mongo_url = os.environ.get("AUTH_TEST_MONGODB_URL")
    redis_url = os.environ.get("AUTH_TEST_REDIS_URL")
    if not mongo_url or not redis_url:
        pytest.skip(
            "Set AUTH_TEST_MONGODB_URL and AUTH_TEST_REDIS_URL for session integration tests"
        )
    mongo = AsyncIOMotorClient(
        mongo_url,
        tz_aware=True,
        directConnection=True,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=10000,
    )
    redis = Redis.from_url(
        redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=3
    )
    name = "test_auth_step_b_" + uuid4().hex
    prefix = name + ":"
    monkeypatch.setattr(settings, "auth_rate_prefix", prefix)
    db = mongo[name]
    connected = False
    previous = app.dependency_overrides.copy()
    try:
        await mongo.admin.command("ping")
        connected = True
        await redis.ping()
        await bootstrap_in_database(db, "admin@example.com", "Admin", "hr", PASSWORD)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_redis] = lambda: redis
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield {"db": db, "redis": redis, "client": client}
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
                    assert name.startswith("test_auth_step_b_")
                    await mongo.drop_database(name)
            finally:
                mongo.close()


async def sign_in(env):
    response = await env["client"].post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    raw = response.cookies.get(settings.refresh_cookie_name)
    return data, raw


def cookie_headers(raw, csrf=None):
    headers = ORIGIN | {"Cookie": f"{settings.refresh_cookie_name}={raw}"}
    if csrf:
        headers["X-CSRF-Token"] = csrf
    return headers


def bearer(data):
    return {"Authorization": "Bearer " + data["access_token"]}


@pytest.mark.asyncio
async def test_login_current_user_and_csrf_restoration(env):
    data, raw = await sign_in(env)
    assert data["user"]["role"] == "admin"
    assert data["expires_in"] == 600
    claims = decode_access_token(data["access_token"])
    assert claims["tv"] == 0
    session = await env["db"].auth_sessions.find_one({"_id": ObjectId(claims["sid"])})
    assert session["current_refresh_hash"] == refresh_digest(raw)
    assert raw not in str(session) and data["csrf_token"] not in str(session)
    response = await env["client"].get("/api/v1/users/me", headers=bearer(data))
    assert response.status_code == 200
    assert "password_hash" not in response.text
    assert response.headers["cache-control"] == "no-store"
    csrf = await env["client"].get("/api/v1/auth/csrf", headers=cookie_headers(raw))
    assert csrf.status_code == 200
    assert csrf.json()["csrf_token"] == data["csrf_token"]
    assert csrf.headers["cache-control"] == "no-store"
    assert "set-cookie" not in csrf.headers


@pytest.mark.asyncio
async def test_multiple_rotations_and_oldest_replay_revoke_family(env):
    data, first = await sign_in(env)
    session_id = ObjectId(decode_access_token(data["access_token"])["sid"])
    original = await env["db"].auth_sessions.find_one({"_id": session_id})
    raw = first
    for _ in range(3):
        response = await env["client"].post(
            "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
        )
        assert response.status_code == 200
        raw = response.cookies.get(settings.refresh_cookie_name)
        assert raw != first
        data = response.json()
    session = await env["db"].auth_sessions.find_one({"_id": session_id})
    assert session["rotation_counter"] == 3
    assert len(session["consumed_refresh_hashes"]) == 3
    assert session["expires_at"] == original["expires_at"]
    replay = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(first, data["csrf_token"])
    )
    assert replay.status_code == 401
    assert (await env["db"].auth_sessions.find_one({"_id": session_id}))[
        "revoke_reason"
    ] == "refresh_reuse"
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(data))
    ).status_code == 401


@pytest.mark.asyncio
async def test_real_concurrent_refresh_never_leaves_two_usable_tokens(env):
    data, raw = await sign_in(env)
    responses = await asyncio.gather(
        *[
            env["client"].post(
                "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
            )
            for _ in range(2)
        ]
    )
    assert sum(r.status_code == 200 for r in responses) <= 1
    assert all(r.status_code in {200, 401} for r in responses)
    session = await env["db"].auth_sessions.find_one({})
    assert session["rotation_counter"] == 1
    assert session["revoked_at"] is not None
    for response in responses:
        if response.status_code == 200:
            assert (
                await env["client"].get(
                    "/api/v1/users/me", headers=bearer(response.json())
                )
            ).status_code == 401


@pytest.mark.asyncio
async def test_unknown_refresh_does_not_revoke_real_session(env):
    data, _raw = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers("z" * 64, data["csrf_token"])
    )
    assert response.status_code == 401
    assert (await env["db"].auth_sessions.find_one({}))["revoked_at"] is None
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(data))
    ).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"status": "DISABLED"},
        {"role": "EMPLOYEE"},
        {"token_version": 1},
        {"department_id": ObjectId()},
    ],
)
async def test_current_account_changes_invalidate_access_and_refresh(env, change):
    data, raw = await sign_in(env)
    await env["db"].users.update_one({}, {"$set": change})
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(data))
    ).status_code == 401
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_only_current_session_and_is_idempotent(env):
    first, first_raw = await sign_in(env)
    second, _ = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/logout", headers=cookie_headers(first_raw, first["csrf_token"])
    )
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(first))
    ).status_code == 401
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(second))
    ).status_code == 200
    env["client"].cookies.clear()
    assert (
        await env["client"].post("/api/v1/auth/logout", headers=ORIGIN)
    ).status_code == 204


@pytest.mark.asyncio
async def test_logout_all_invalidates_all_sessions_and_allows_fresh_login(env):
    first, _ = await sign_in(env)
    second, raw = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/logout-all",
        headers=cookie_headers(raw, second["csrf_token"]) | bearer(second),
    )
    assert response.status_code == 204
    assert (await env["db"].users.find_one({}))["token_version"] == 1
    for data in (first, second):
        assert (
            await env["client"].get("/api/v1/users/me", headers=bearer(data))
        ).status_code == 401
    fresh, _ = await sign_in(env)
    assert decode_access_token(fresh["access_token"])["tv"] == 1
    assert (
        await env["client"].get("/api/v1/users/me", headers=bearer(fresh))
    ).status_code == 200


@pytest.mark.asyncio
async def test_logout_all_epoch_covers_session_inserted_after_sweep(env):
    _data, _ = await sign_in(env)
    user = await env["db"].users.find_one({})
    old_session = await env["db"].auth_sessions.find_one({})
    await logout_all(env["db"], user, "race-test")
    # A login that read epoch 0 can insert after the logout-all sweep.
    late = old_session | {
        "_id": ObjectId(),
        "revoked_at": None,
        "current_refresh_hash": "late-test-hash",
    }
    await env["db"].auth_sessions.insert_one(late)
    from app.auth.service import active_session_user

    with pytest.raises(HTTPException) as error:
        await active_session_user(env["db"], late)
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_csrf_and_origin_failures_do_not_rotate(env):
    data, raw = await sign_in(env)
    client = env["client"]
    for headers in (
        {
            "Cookie": f"{settings.refresh_cookie_name}={raw}",
            "X-CSRF-Token": data["csrf_token"],
        },
        cookie_headers(raw, data["csrf_token"])
        | {"Origin": "https://attacker.example"},
        cookie_headers(raw),
        cookie_headers(raw, "a" * 64),
    ):
        assert (
            await client.post("/api/v1/auth/refresh", headers=headers)
        ).status_code == 403
    assert (await env["db"].auth_sessions.find_one({}))["rotation_counter"] == 0
    assert (
        await client.get(
            "/api/v1/auth/csrf",
            headers={"Cookie": f"{settings.refresh_cookie_name}={raw}"},
        )
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/auth/login",
            headers={"Origin": "null"},
            json={"email": "admin@example.com", "password": PASSWORD},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_csrf_is_bound_to_session(env):
    first, _ = await sign_in(env)
    _, second_raw = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(second_raw, first["csrf_token"])
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_generic_login_failures_and_validation_do_not_echo_password(env):
    client = env["client"]
    bad = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": "incorrect"},
    )
    missing = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "missing@example.com", "password": "incorrect"},
    )
    await env["db"].users.update_one({}, {"$set": {"status": "DISABLED"}})
    disabled = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": PASSWORD},
    )
    assert bad.status_code == missing.status_code == disabled.status_code == 401
    assert (
        bad.json()["error"]["message"]
        == missing.json()["error"]["message"]
        == disabled.json()["error"]["message"]
    )
    response = await client.post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": "sensitive-value-" * 20},
    )
    assert response.status_code == 422 and "sensitive-value" not in response.text
    assert await env["db"].auth_sessions.count_documents({}) == 0


@pytest.mark.asyncio
async def test_expired_session_and_rotation_cap_require_login(env, monkeypatch):
    data, raw = await sign_in(env)
    await env["db"].auth_sessions.update_one(
        {}, {"$set": {"expires_at": utcnow() - timedelta(seconds=1)}}
    )
    assert (
        await env["client"].post(
            "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
        )
    ).status_code == 401
    new, raw = await sign_in(env)
    monkeypatch.setattr(settings, "max_refresh_rotations", 1)
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(raw, new["csrf_token"])
    )
    assert response.status_code == 200
    response = await env["client"].post(
        "/api/v1/auth/refresh",
        headers=cookie_headers(
            response.cookies.get(settings.refresh_cookie_name), new["csrf_token"]
        ),
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims_change",
    [
        {"aud": "wrong"},
        {"iss": "wrong"},
        {"typ": "refresh"},
        {"tv": True},
        {"sid": "invalid"},
        {"exp": 1},
    ],
)
async def test_jwt_claim_rejection(env, claims_change):
    data, _ = await sign_in(env)
    claims = decode_access_token(data["access_token"]) | claims_change
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")
    response = await env["client"].get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_atomic_limiter_and_ttl_recovery(env):
    async def attempt():
        try:
            await rate_limit(env["redis"], "isolated-test-account", 5, 1)
            return 200
        except HTTPException as error:
            assert error.headers["Retry-After"] == "1"
            return error.status_code

    statuses = await asyncio.gather(*[attempt() for _ in range(20)])
    assert statuses.count(200) == 5 and statuses.count(429) == 15
    keys = [
        key
        async for key in env["redis"].scan_iter(match=settings.auth_rate_prefix + "*")
    ]
    assert len(keys) == 1
    assert "isolated-test-account" not in keys[0]
    assert await env["redis"].pttl(keys[0]) > 0
    await env["redis"].pexpire(keys[0], 1)
    await asyncio.sleep(0.03)
    await rate_limit(env["redis"], "isolated-test-account", 5, 1)


@pytest.mark.asyncio
async def test_login_endpoint_rate_limits(env, monkeypatch):
    monkeypatch.setattr(settings, "login_rate_limit", 1)
    await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": PASSWORD},
    )
    assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_redis_failure_fails_closed(env):
    class UnavailableRedis:
        async def eval(self, *args):
            raise RedisConnectionError("unavailable")

    app.dependency_overrides[get_redis] = lambda: UnavailableRedis()
    response = await env["client"].post(
        "/api/v1/auth/login",
        headers=ORIGIN,
        json={"email": "admin@example.com", "password": PASSWORD},
    )
    assert response.status_code == 503
    assert await env["db"].auth_sessions.count_documents({}) == 0


@pytest.mark.asyncio
async def test_audit_failure_does_not_return_usable_login(env):
    class MissingAudit:
        async def insert_one(self, *args):
            raise ServerSelectionTimeoutError("test unavailable audit")

    class Database:
        audit_logs = MissingAudit()

        def __getattr__(self, key):
            return getattr(env["db"], key)

    with pytest.raises(HTTPException) as error:
        await login(Database(), "admin@example.com", PASSWORD)
    assert error.value.status_code == 503
    assert (await env["db"].auth_sessions.find_one({}))["revoked_at"] is not None


@pytest.mark.asyncio
async def test_cookies_cors_and_audit_privacy(env, monkeypatch):
    data, raw = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
    )
    cookie = response.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie
        and "SameSite=lax" in cookie
        and "Path=/api/v1/auth" in cookie
    )
    assert "Secure" not in cookie
    assert response.headers["access-control-allow-origin"] == ORIGIN["Origin"]
    monkeypatch.setattr(settings, "cookie_secure", True)
    secure = await env["client"].post(
        "/api/v1/auth/refresh",
        headers=cookie_headers(
            response.cookies.get(settings.refresh_cookie_name), data["csrf_token"]
        ),
    )
    assert "Secure" in secure.headers["set-cookie"]
    records = await env["db"].audit_logs.find({}).to_list(length=100)
    for value in (PASSWORD, raw, data["access_token"], data["csrf_token"]):
        assert value not in str(records)
    assert all(record["metadata"] == {} for record in records)
    assert {"auth.login", "auth.refresh"} <= {r["event"] for r in records}


def test_production_and_staging_configuration():
    for stage in ("production", "staging"):
        with pytest.raises(RuntimeError):
            validate_security_settings(Settings(app_env=stage))
        validate_security_settings(
            Settings(
                app_env=stage,
                cookie_secure=True,
                jwt_secret_key="x" * 48,
                frontend_origin="https://app.example.com",
            )
        )


def test_referer_fallback_validates_exact_origin():
    from types import SimpleNamespace

    require_origin(SimpleNamespace(headers={"referer": "http://localhost:3000/login"}))
    for origin in (
        "http://localhost:3000.attacker.com",
        "http://localhost:3000@attacker.com",
    ):
        with pytest.raises(HTTPException):
            require_origin(SimpleNamespace(headers={"referer": origin}))


@pytest.mark.asyncio
async def test_cookie_and_jwt_cannot_extend_absolute_session_deadline(env):
    from http.cookies import SimpleCookie

    data, raw = await sign_in(env)
    deadline = utcnow() + timedelta(seconds=30)
    await env["db"].auth_sessions.update_one({}, {"$set": {"expires_at": deadline}})
    response = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
    )
    assert response.status_code == 200
    cookies = SimpleCookie()
    cookies.load(response.headers["set-cookie"])
    assert 1 <= int(cookies[settings.refresh_cookie_name]["max-age"]) <= 30
    assert decode_access_token(response.json()["access_token"])["exp"] <= int(
        deadline.timestamp()
    )
    assert 1 <= response.json()["expires_in"] <= 30


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["EMPLOYEE", "MANAGER"])
async def test_backend_role_checks_ignore_client_role_claims(env, role):
    await env["db"].users.update_one({}, {"$set": {"role": role}})
    data, _ = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/admin/users",
        headers=bearer(data),
        json={
            "email": "new@example.com",
            "display_name": "New",
            "password": PASSWORD,
            "department_id": str(
                (await env["db"].departments.find_one({"code": "HR"}))["_id"]
            ),
            "role": "ADMIN",
        },
    )
    assert response.status_code == 403
    assert await env["db"].users.count_documents({}) == 1
    assert (
        await env["db"].audit_logs.count_documents(
            {"event": "auth.authorization", "outcome": "DENIED"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_session_cannot_be_used_for_another_user(env):
    data, _ = await sign_in(env)
    admin = await env["db"].users.find_one({})
    other = UserRecord(
        email_normalized="other@example.com",
        name="Other",
        role="ADMIN",
        password_hash=admin["password_hash"],
        department_id=admin["department_id"],
        created_by=admin["_id"],
    ).bson()
    await env["db"].users.insert_one(other)
    claims = decode_access_token(data["access_token"]) | {"sub": str(other["_id"])}
    forged = jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")
    response = await env["client"].get(
        "/api/v1/users/me", headers={"Authorization": "Bearer " + forged}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_signature_missing_claim_and_tampered_token(env):
    data, _ = await sign_in(env)
    claims = decode_access_token(data["access_token"])
    missing = dict(claims)
    missing.pop("tv")
    tokens = [
        jwt.encode(claims, "incorrect-signing-key-" * 3, algorithm="HS256"),
        jwt.encode(missing, settings.jwt_secret_key, algorithm="HS256"),
        "invalid.jwt.value",
    ]
    for token in tokens:
        assert (
            await env["client"].get(
                "/api/v1/users/me", headers={"Authorization": "Bearer " + token}
            )
        ).status_code == 401


@pytest.mark.asyncio
async def test_request_ids_do_not_copy_sensitive_client_headers(env):
    data, raw = await sign_in(env)
    response = await env["client"].get(
        "/api/v1/users/me", headers=bearer(data) | {"X-Request-ID": raw}
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] != raw


@pytest.mark.asyncio
async def test_refresh_audit_failure_revokes_rotated_session(env):
    from app.auth.service import rotate_refresh

    data, raw = await sign_in(env)

    class MissingAudit:
        async def insert_one(self, *args):
            raise ServerSelectionTimeoutError("test unavailable audit")

    class Database:
        audit_logs = MissingAudit()

        def __getattr__(self, key):
            return getattr(env["db"], key)

    with pytest.raises(HTTPException) as error:
        await rotate_refresh(
            Database(), decode_access_token(data["access_token"])["sid"], raw
        )
    assert error.value.status_code == 503
    assert (await env["db"].auth_sessions.find_one({}))["revoked_at"] is not None


@pytest.mark.asyncio
async def test_cookie_session_and_bearer_session_must_match_for_logout_all(env):
    first, _ = await sign_in(env)
    second, raw = await sign_in(env)
    response = await env["client"].post(
        "/api/v1/auth/logout-all",
        headers=cookie_headers(raw, second["csrf_token"]) | bearer(first),
    )
    assert response.status_code == 403
    assert (await env["db"].users.find_one({}))["token_version"] == 0


@pytest.mark.asyncio
async def test_preflight_allows_csrf_header_only_for_configured_origin(env):
    request_headers = {
        "Origin": ORIGIN["Origin"],
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
    }
    response = await env["client"].options(
        "/api/v1/auth/refresh", headers=request_headers
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["access-control-allow-origin"] == ORIGIN["Origin"]
    response = await env["client"].options(
        "/api/v1/auth/refresh",
        headers=request_headers | {"Origin": "https://attacker.example"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_refresh_session_rate_limit_and_audit(env, monkeypatch):
    data, raw = await sign_in(env)
    monkeypatch.setattr(settings, "refresh_rate_limit", 1)
    first = await env["client"].post(
        "/api/v1/auth/refresh", headers=cookie_headers(raw, data["csrf_token"])
    )
    assert first.status_code == 200
    response = await env["client"].post(
        "/api/v1/auth/refresh",
        headers=cookie_headers(
            first.cookies.get(settings.refresh_cookie_name), data["csrf_token"]
        ),
    )
    assert response.status_code == 429
    assert (
        await env["db"].audit_logs.count_documents(
            {"event": "auth.rate_limit", "outcome": "DENIED"}
        )
        == 1
    )
