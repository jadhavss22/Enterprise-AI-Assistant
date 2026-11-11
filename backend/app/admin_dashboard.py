import asyncio
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app.auth.audit import record_audit
from app.auth.dependencies import get_db, get_redis, require_roles
from app.auth.models import Role, utcnow
from app.auth.rate_limits import rate_limit
from app.config import settings
from app.retrieval.readiness import model_readiness

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def counts(collection, query, group, labels):
    async with asyncio.timeout(4):
        rows = await collection.aggregate(
            [{"$match": query}, {"$group": {"_id": group, "count": {"$sum": 1}}}],
            maxTimeMS=3000,
        ).to_list(length=len(labels))
    result = dict.fromkeys(labels, 0)
    for row in rows:
        if row["_id"] in result:
            result[row["_id"]] = row["count"]
    return result


async def documents(db):
    # All stored statuses are included, including future status values.
    async with asyncio.timeout(4):
        rows = await db.documents.aggregate(
            [
                {"$match": {"deleted_at": None}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ],
            maxTimeMS=3000,
        ).to_list(length=None)
    by_status = {row["_id"] or "UNKNOWN": row["count"] for row in rows}
    return {"total": sum(by_status.values()), "by_status": by_status}


async def users(db):
    active, disabled = await asyncio.gather(
        counts(db.users, {"status": "ACTIVE"}, "$role", [role.value for role in Role]),
        db.users.count_documents({"status": "DISABLED"}, maxTimeMS=3000),
    )
    return {"active_by_role": active, "disabled": disabled}


async def chat(db, window):
    conversations, turns = await asyncio.gather(
        db.conversations.count_documents(
            {"created_at": window, "deleted_at": None}, maxTimeMS=3000
        ),
        db.chat_turns.aggregate(
            [
                {"$match": {"created_at": window}},
                {"$group": {
                    "_id": None,
                    "questions": {"$sum": 1},
                    "completed": {"$sum": {"$cond": [
                        {"$eq": ["$status", "COMPLETED"]}, 1, 0
                    ]}},
                }},
            ],
            maxTimeMS=3000,
        ).to_list(length=1),
    )
    turn_counts = turns[0] if turns else {"questions": 0, "completed": 0}
    return {
        "conversations": conversations,
        "questions": turn_counts["questions"],
        "completed": turn_counts["completed"],
    }


async def panel(operation):
    try:
        async with asyncio.timeout(5):
            return await operation
    except (PyMongoError, TimeoutError):
        return None


async def worker_status(redis):
    try:
        async with asyncio.timeout(2):
            result = await model_readiness(redis)
    except (RedisError, TimeoutError):
        return "unknown"
    return {
        "ready": "healthy", "warming": "starting",
        "failed": "unavailable", "unavailable": "unavailable",
    }.get(result["status"], "unknown")


@router.get("/dashboard")
async def dashboard(
    request: Request,
    days: Literal["7", "30"] = "7",
    actor=Depends(require_roles(Role.ADMIN)),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(redis, f"admin-dashboard:{actor['_id']}", settings.admin_read_rate_limit, 60)
    await record_audit(
        db, "admin.dashboard.read", "ALLOWED",
        user_id=actor["_id"], request_id=request.state.request_id,
    )
    now = utcnow()
    start = now - timedelta(days=int(days))
    window = {"$gte": start, "$lt": now}
    doc_counts, failed, user_counts, usage, feedback, worker = await asyncio.gather(
        panel(documents(db)),
        panel(db.ingestion_jobs.count_documents(
            {"status": "FAILED", "updated_at": window}, maxTimeMS=3000
        )),
        panel(users(db)),
        panel(chat(db, window)),
        panel(counts(db.feedback, {"created_at": window}, "$rating", ["THUMBS_UP", "THUMBS_DOWN"])),
        worker_status(redis),
    )
    return {
        "days": int(days),
        "window_start": start,
        "checked_at": utcnow(),
        "documents": doc_counts,
        "failed_jobs": failed,
        "users": user_counts,
        "chat": usage,
        "feedback": feedback,
        "health": {"api": "healthy", "worker": worker},
    }
