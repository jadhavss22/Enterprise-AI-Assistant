from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.auth.models import SEED_DEPARTMENTS, utcnow


async def ensure_auth_storage(db: AsyncIOMotorDatabase) -> None:
    await db.departments.create_index("slug", unique=True)
    await db.users.create_index("email", unique=True)
    await db.users.create_index([("department_id", ASCENDING), ("status", ASCENDING)])
    await db.sessions.create_index("session_id", unique=True)
    await db.sessions.create_index([("user_id", ASCENDING), ("expires_at", DESCENDING)])
    await db.sessions.create_index([("family_id", ASCENDING), ("expires_at", DESCENDING)])
    await db.audit_events.create_index([("created_at", DESCENDING)])
    await db.audit_events.create_index([("actor_user_id", ASCENDING), ("created_at", DESCENDING)])

    now = utcnow()
    for department in SEED_DEPARTMENTS:
        await db.departments.update_one(
            {"slug": department["slug"]},
            {
                "$setOnInsert": {
                    **department,
                    "active": True,
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
