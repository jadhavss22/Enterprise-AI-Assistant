from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from app.auth.models import SEED_DEPARTMENTS, DepartmentRecord


async def assert_supported_auth_schema(db: AsyncIOMotorDatabase) -> None:
    """Fail before changing indexes/data if a pre-SDD database needs migration."""
    for collection in ("sessions", "audit_events"):
        if await db[collection].count_documents({}, limit=1):
            raise RuntimeError(
                "Legacy authentication data requires an explicit offline migration; no data was changed"
            )
    checks = (
        (
            "users",
            {
                "$or": [
                    {"email_normalized": {"$exists": False}},
                    {"name": {"$exists": False}},
                    {"_id": {"$not": {"$type": "objectId"}}},
                    {"created_by": {"$not": {"$type": "objectId"}}},
                    {"token_version": {"$exists": False}},
                    {"role": {"$nin": ["EMPLOYEE", "MANAGER", "ADMIN"]}},
                    {"status": {"$nin": ["ACTIVE", "DISABLED"]}},
                    {"department_id": {"$not": {"$type": "objectId"}}},
                ]
            },
        ),
        (
            "departments",
            {
                "$or": [
                    {"code": {"$exists": False}},
                    {"status": {"$nin": ["ACTIVE", "DISABLED"]}},
                ]
            },
        ),
    )
    for collection, query in checks:
        if await db[collection].count_documents(query, limit=1):
            raise RuntimeError(
                "Legacy authentication schema requires an explicit offline migration; no data was changed"
            )


async def ensure_auth_storage(db: AsyncIOMotorDatabase) -> None:
    await assert_supported_auth_schema(db)
    await db.departments.create_index("code", unique=True)
    await db.users.create_index("email_normalized", unique=True)
    await db.users.create_index([("created_at", DESCENDING), ("_id", DESCENDING)])
    await db.auth_admin_guard.update_one(
        {"_id": "admin-user-writes"}, {"$setOnInsert": {"revision": 0}}, upsert=True
    )
    await db.users.create_index(
        "bootstrap_key",
        unique=True,
        partialFilterExpression={"bootstrap_key": "first-admin"},
    )
    await db.users.create_index([("department_id", ASCENDING), ("status", ASCENDING)])
    await db.auth_sessions.create_index("current_refresh_hash", unique=True)
    await db.auth_sessions.create_index("consumed_refresh_hashes")
    await db.auth_sessions.create_index("expires_at", expireAfterSeconds=0)
    await db.auth_sessions.create_index(
        [("user_id", ASCENDING), ("revoked_at", ASCENDING)]
    )
    await db.auth_sessions.create_index("token_family_id")
    await db.audit_logs.create_index(
        [("actor_id", ASCENDING), ("created_at", DESCENDING)]
    )
    await db.audit_logs.create_index([("event", ASCENDING), ("created_at", DESCENDING)])
    await db.audit_logs.create_index("request_id")
    for department in SEED_DEPARTMENTS:
        record = DepartmentRecord(**department).bson()
        try:
            await db.departments.update_one(
                {"code": department["code"]}, {"$setOnInsert": record}, upsert=True
            )
        except DuplicateKeyError:
            # Another process may have won the same idempotent seed upsert.
            if not await db.departments.find_one({"code": department["code"]}):
                raise
