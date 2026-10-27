"""Real MongoDB tests. Set AUTH_TEST_MONGODB_URL to opt in."""

import asyncio
import os
from uuid import uuid4

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.auth.bootstrap_admin import bootstrap_in_database
from app.auth.schemas import BootstrapInput
from app.auth.security import verify_password
from app.auth.storage import ensure_auth_storage


def test_input_validation_and_password_redaction():
    data = BootstrapInput(
        email=" ADMIN@example.com ",
        display_name=" Admin ",
        department_slug=" HR ",
        password="a long test password",
    )
    assert data.email == "admin@example.com"
    assert "password" not in data.model_dump()
    for password in ("short", "x" * 129):
        with pytest.raises(ValueError):
            BootstrapInput(
                email=data.email,
                display_name="Admin",
                department_slug="hr",
                password=password,
            )


@pytest.mark.asyncio
async def test_real_mongo_bootstrap():
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
    db = client[name]
    password = "test-only long password " + uuid4().hex
    connected = False
    try:
        await client.admin.command("ping")
        connected = True
        await ensure_auth_storage(db)
        await ensure_auth_storage(db)
        assert await db.departments.count_documents({}) == 3
        await db.departments.update_one(
            {"code": "HR"}, {"$set": {"status": "DISABLED"}}
        )
        await ensure_auth_storage(db)
        assert (await db.departments.find_one({"code": "HR"}))["status"] == "DISABLED"
        for department in ("hr", "missing"):
            with pytest.raises(ValueError):
                await bootstrap_in_database(
                    db, "admin@example.com", "Admin", department, password
                )
        assert await db.users.count_documents({}) == 0
        results = await asyncio.gather(
            *[
                bootstrap_in_database(
                    db, f"admin{i}@example.com", "Admin", "engineering", password
                )
                for i in range(4)
            ],
            return_exceptions=True,
        )
        assert sum(isinstance(x, str) for x in results) == 1
        assert sum(isinstance(x, ValueError) for x in results) == 3
        user = await db.users.find_one({})
        assert await db.users.count_documents({}) == 1
        assert (
            user["department_id"]
            == (await db.departments.find_one({"code": "ENGINEERING"}))["_id"]
        )
        assert user["role"] == "ADMIN"
        assert user["token_version"] == 0
        assert user["password_hash"].startswith("$argon2id$")
        assert verify_password(user["password_hash"], password)
        assert not verify_password(user["password_hash"], "incorrect")
        assert password not in str(user)
        assert await db.audit_logs.count_documents({"outcome": "SUCCESS"}) == 1
        # Simulate a crash between account insertion and audit creation.
        await db.audit_logs.delete_one({"_id": user["_id"]})
        await db.users.update_one(
            {"_id": user["_id"]}, {"$set": {"status": "DISABLED"}}
        )
        with pytest.raises(ValueError):
            await bootstrap_in_database(
                db, "another@example.com", "Admin", "engineering", password
            )
        assert await db.audit_logs.count_documents({"outcome": "SUCCESS"}) == 1
        assert await db.users.count_documents({}) == 1
        assert (await db.users.find_one({"_id": user["_id"]}))["password_hash"] == user[
            "password_hash"
        ]
        duplicate = dict(user)
        duplicate.pop("_id")
        duplicate.pop("bootstrap_key")
        with pytest.raises(DuplicateKeyError):
            await db.users.insert_one(duplicate)
    finally:
        # Only this randomly named disposable test database is removed.
        assert name.startswith("test_auth_step_a_")
        try:
            if connected:
                await client.drop_database(name)
        finally:
            client.close()
