"""Persistence contracts exercised against a disposable real MongoDB database."""

import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi.security import HTTPAuthorizationCredentials
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.dependencies import current_user
from app.auth.models import AuthSessionRecord, Role, UserRecord, utcnow
from app.auth.schemas import public_user
from app.auth.service import login, rotate_refresh
from app.auth.storage import ensure_auth_storage
from app.auth.tokens import decode_access_token


@pytest_asyncio.fixture
async def auth_db():
    url = os.environ.get("AUTH_TEST_MONGODB_URL")
    if not url:
        pytest.skip("Set AUTH_TEST_MONGODB_URL for real MongoDB integration")
    client = AsyncIOMotorClient(
        url,
        tz_aware=True,
        directConnection=True,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=3000,
    )
    name = "test_auth_step_a_" + uuid4().hex
    connected = False
    try:
        await client.admin.command("ping")
        connected = True
        yield client[name]
    finally:
        try:
            if connected:
                assert name.startswith("test_auth_step_a_")
                await client.drop_database(name)
        finally:
            client.close()


def test_persistence_model_requires_objectid_and_known_role():
    common = {
        "email_normalized": "user@example.com",
        "name": "User",
        "password_hash": "encoded-test-hash",
        "department_id": ObjectId(),
        "created_by": ObjectId(),
    }
    record = UserRecord(**common, role=Role.EMPLOYEE)
    assert isinstance(record.bson()["department_id"], ObjectId)
    assert record.bson()["role"] == "EMPLOYEE"
    assert "password_hash" not in public_user(record.bson()).model_dump()
    assert public_user(record.bson()).role == "employee"
    assert "encoded-test-hash" not in repr(record)
    for overrides in (
        {"department_id": "not-an-objectid"},
        {"email_normalized": "UPPER@example.com"},
        {"role": "superadmin"},
        {"token_version": -1},
    ):
        with pytest.raises(ValidationError):
            UserRecord(**(common | {"role": Role.EMPLOYEE} | overrides))


@pytest.mark.asyncio
async def test_unique_user_and_session_indexes(auth_db):
    await ensure_auth_storage(auth_db)
    department = await auth_db.departments.find_one({"code": "ENGINEERING"})
    user = UserRecord(
        email_normalized="user@example.com",
        name="User",
        password_hash="encoded-test-hash",
        role=Role.EMPLOYEE,
        department_id=department["_id"],
        created_by=ObjectId(),
    ).bson()
    await auth_db.users.insert_one(user)
    duplicate = user | {"_id": ObjectId()}
    with pytest.raises(DuplicateKeyError):
        await auth_db.users.insert_one(duplicate)
    session = AuthSessionRecord(
        user_id=user["_id"],
        current_refresh_hash="test-hash",
        role_at_login=Role.EMPLOYEE,
        department_at_login=department["_id"],
        expires_at=utcnow() + timedelta(days=1),
    ).bson()
    await auth_db.auth_sessions.insert_one(session)
    with pytest.raises(DuplicateKeyError):
        await auth_db.auth_sessions.insert_one(session | {"_id": ObjectId()})
    indexes = await auth_db.auth_sessions.index_information()
    assert indexes["expires_at_1"]["expireAfterSeconds"] == 0
    assert indexes["current_refresh_hash_1"]["unique"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "collection", ["users", "sessions", "audit_events", "departments"]
)
async def test_legacy_data_is_refused_without_mutation(auth_db, collection):
    original = {"_id": ObjectId(), "legacy_marker": "preserve-me"}
    await auth_db[collection].insert_one(original)
    with pytest.raises(RuntimeError, match="explicit offline migration"):
        await ensure_auth_storage(auth_db)
    assert await auth_db[collection].find_one({}) == original
    assert await auth_db[collection].count_documents({}) == 1
    assert "code_1" not in await auth_db.departments.index_information()


@pytest.mark.asyncio
async def test_bootstrap_refuses_preexisting_disabled_admin(auth_db):
    await ensure_auth_storage(auth_db)
    department = await auth_db.departments.find_one({"code": "HR"})
    existing = UserRecord(
        email_normalized="old@example.com",
        name="Existing",
        password_hash="unchanged-test-hash",
        role=Role.ADMIN,
        status="DISABLED",
        department_id=department["_id"],
        created_by=ObjectId(),
    ).bson()
    await auth_db.users.insert_one(existing)
    existing = await auth_db.users.find_one({"_id": existing["_id"]})
    with pytest.raises(ValueError):
        await bootstrap_in_database(
            auth_db, "new@example.com", "New", "hr", "a long test-only password"
        )
    assert await auth_db.users.find_one({}) == existing
    assert await auth_db.users.count_documents({}) == 1
    event = await auth_db.audit_logs.find_one({"outcome": "DENIED"})
    assert event["metadata"] == {"reason": "admin_exists"}
    assert "password" not in str(event)


@pytest.mark.asyncio
async def test_existing_auth_code_reads_canonical_persistence(auth_db):
    # Compatibility regression only; full auth/security acceptance remains Step B.
    password = "a long test-only password"
    await bootstrap_in_database(auth_db, "admin@example.com", "Admin", "hr", password)
    response, refresh, _ = await login(auth_db, "admin@example.com", password)
    assert response.user.role == "admin"
    claims = decode_access_token(response.access_token)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=response.access_token
    )
    user = await current_user(credentials, auth_db)
    assert str(user["_id"]) == claims["sub"]
    session = await auth_db.auth_sessions.find_one({"_id": ObjectId(claims["sid"])})
    assert session["user_id"] == user["_id"]
    assert refresh not in str(session)
    next_response, _, _ = await rotate_refresh(auth_db, claims["sid"], refresh)
    assert next_response.user.user_id == response.user.user_id
    assert (await auth_db.auth_sessions.find_one({"_id": session["_id"]}))[
        "expires_at"
    ] == session["expires_at"]
    assert "sessions" not in await auth_db.list_collection_names()
    assert "audit_events" not in await auth_db.list_collection_names()


@pytest.mark.asyncio
async def test_duplicate_email_does_not_consume_bootstrap(auth_db):
    await ensure_auth_storage(auth_db)
    department = await auth_db.departments.find_one({"code": "HR"})
    existing = UserRecord(
        email_normalized="employee@example.com",
        name="Employee",
        password_hash="unchanged-test-hash",
        role=Role.EMPLOYEE,
        department_id=department["_id"],
        created_by=ObjectId(),
    ).bson()
    await auth_db.users.insert_one(existing)
    with pytest.raises(ValueError):
        await bootstrap_in_database(
            auth_db,
            " EMPLOYEE@example.com ",
            "Admin",
            "hr",
            "a long test-only password",
        )
    assert await auth_db.users.count_documents({"bootstrap_key": "first-admin"}) == 0
    await bootstrap_in_database(
        auth_db, "admin@example.com", "Admin", "hr", "a long test-only password"
    )
    assert await auth_db.users.count_documents({}) == 2
    assert (await auth_db.users.find_one({"_id": existing["_id"]}))[
        "password_hash"
    ] == "unchanged-test-hash"
