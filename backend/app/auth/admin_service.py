import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException
from pymongo import ReadPreference, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.auth.models import AuditRecord, Role, UserRecord, utcnow
from app.auth.schemas import public_user
from app.auth.security import hash_password
from app.auth.service import session_matches_user
from app.config import settings

GUARD_ID = "admin-user-writes"


def fail(status, code, message):
    return HTTPException(status, detail={"code": code, "message": message})


async def audit_admin(
    db,
    actor_id,
    event,
    outcome,
    request_id,
    *,
    target_id=None,
    fields=None,
    reason=None,
    session=None,
    resource_type="user",
):
    metadata = {}
    if fields is not None:
        metadata["fields"] = sorted(fields)
    if reason:
        metadata["reason"] = reason
    record = AuditRecord(
        actor_id=actor_id,
        event=event,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=target_id,
        request_id=request_id,
        metadata=metadata,
    ).bson()
    try:
        await db.audit_logs.insert_one(record, session=session)
    except PyMongoError:
        raise fail(503, "AUDIT_UNAVAILABLE", "Security audit unavailable") from None


async def run_admin_write(db, actor, request_id, event, target_id, operation):
    async def transaction(session):
        # All Admin mutations write this row. Snapshot write skew becomes a
        # retryable write conflict, so simultaneous last-Admin removals serialize.
        guard = await db.auth_admin_guard.update_one(
            {"_id": GUARD_ID}, {"$inc": {"revision": 1}}, session=session
        )
        if guard.matched_count != 1:
            raise fail(503, "ADMIN_STORAGE_UNREADY", "Admin storage is not initialized")
        current = await db.users.find_one({"_id": actor["_id"]}, session=session)
        active = await db.auth_sessions.find_one(
            {"_id": actor["_auth_session"]["_id"]}, session=session
        )
        if (
            not session_matches_user(active, current)
            or current["role"] != Role.ADMIN.value
        ):
            raise fail(403, "ACCESS_DENIED", "Admin authorization changed")
        return await operation(session)

    try:
        async with asyncio.timeout(settings.admin_transaction_timeout_seconds):
            async with await db.client.start_session() as session:
                return await session.with_transaction(
                    transaction,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY,
                    max_commit_time_ms=5000,
                )
    except DuplicateKeyError:
        await audit_admin(
            db,
            actor["_id"],
            event,
            "DENIED",
            request_id,
            target_id=target_id,
            reason="EMAIL_EXISTS",
        )
        raise fail(409, "EMAIL_EXISTS", "Email is already registered") from None
    except TimeoutError:
        await audit_admin(
            db,
            actor["_id"],
            event,
            "FAILED",
            request_id,
            target_id=target_id,
            reason="TRANSACTION_TIMEOUT",
        )
        raise fail(
            503,
            "ADMIN_BUSY",
            "Admin operation timed out; check current state before retrying",
        ) from None
    except HTTPException as error:
        code = (
            error.detail.get("code", "ADMIN_FAILED")
            if isinstance(error.detail, dict)
            else "ADMIN_FAILED"
        )
        await audit_admin(
            db,
            actor["_id"],
            event,
            "DENIED" if error.status_code < 500 else "FAILED",
            request_id,
            target_id=target_id,
            reason=code,
        )
        raise


async def create_user(db, actor, payload, request_id):
    # Hash once outside the retryable transaction; never log credentials.
    password_hash = await asyncio.to_thread(
        hash_password, payload.password.get_secret_value()
    )
    user = UserRecord(
        email_normalized=payload.email,
        name=payload.display_name,
        password_hash=password_hash,
        role=payload.role,
        department_id=ObjectId(payload.department_id),
        created_by=actor["_id"],
    ).bson()

    async def operation(session):
        if not await db.departments.find_one(
            {"_id": user["department_id"], "status": "ACTIVE"}, session=session
        ):
            raise fail(422, "DEPARTMENT_INVALID", "An active department is required")
        if await db.users.find_one(
            {"email_normalized": user["email_normalized"]}, session=session
        ):
            raise fail(409, "EMAIL_EXISTS", "Email is already registered")
        await db.users.insert_one(user, session=session)
        await audit_admin(
            db,
            actor["_id"],
            "admin.user_created",
            "SUCCESS",
            request_id,
            target_id=user["_id"],
            fields=["name", "role", "department_id"],
            session=session,
        )
        return public_user(user)

    return await run_admin_write(
        db, actor, request_id, "admin.user_created", None, operation
    )


async def update_user(db, actor, user_id, payload, request_id):
    target_id = ObjectId(user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "display_name" in changes:
        changes["name"] = changes.pop("display_name")
    if "department_id" in changes:
        changes["department_id"] = ObjectId(changes["department_id"])

    async def operation(session):
        user = await db.users.find_one({"_id": target_id}, session=session)
        if not user:
            raise fail(404, "USER_NOT_FOUND", "User not found")
        actual = {key: value for key, value in changes.items() if value != user[key]}
        if "department_id" in actual and not await db.departments.find_one(
            {"_id": actual["department_id"], "status": "ACTIVE"}, session=session
        ):
            raise fail(422, "DEPARTMENT_INVALID", "An active department is required")
        final_role, final_status = (
            actual.get("role", user["role"]),
            actual.get("status", user["status"]),
        )
        removing_admin = (
            user["role"] == "ADMIN"
            and user["status"] == "ACTIVE"
            and (final_role != "ADMIN" or final_status != "ACTIVE")
        )
        if removing_admin:
            count = await db.users.count_documents(
                {"role": "ADMIN", "status": "ACTIVE"}, session=session
            )
            if count <= 1:
                raise fail(
                    409, "LAST_ACTIVE_ADMIN", "At least one active Admin must remain"
                )
        sensitive = bool({"role", "status", "department_id"} & actual.keys())
        updated = user
        if actual:
            command = {"$set": actual | {"updated_at": utcnow()}}
            if sensitive:
                command["$inc"] = {"token_version": 1}
            updated = await db.users.find_one_and_update(
                {"_id": target_id},
                command,
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if sensitive:
                await db.auth_sessions.update_many(
                    {
                        "user_id": target_id,
                        "token_version": {"$lt": updated["token_version"]},
                        "revoked_at": None,
                    },
                    {
                        "$set": {
                            "revoked_at": utcnow(),
                            "revoke_reason": "admin_user_changed",
                        }
                    },
                    session=session,
                )
        await audit_admin(
            db,
            actor["_id"],
            "admin.user_updated",
            "SUCCESS",
            request_id,
            target_id=target_id,
            fields=actual.keys(),
            session=session,
        )
        return public_user(updated)

    return await run_admin_write(
        db, actor, request_id, "admin.user_updated", target_id, operation
    )


def cursor_fingerprint(filters):
    return hashlib.sha256(
        json.dumps(
            {key: str(value) for key, value in filters.items()}, sort_keys=True
        ).encode()
    ).hexdigest()


def encode_cursor(user, filters):
    payload = json.dumps(
        {
            "at": user["created_at"].isoformat(),
            "id": str(user["_id"]),
            "filters": cursor_fingerprint(filters),
        },
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(
        settings.jwt_secret_key.encode(),
        ("admin-cursor:" + encoded).encode(),
        hashlib.sha256,
    ).hexdigest()
    return encoded + "." + signature


def decode_cursor(cursor, filters):
    try:
        if len(cursor) > 1024:
            raise ValueError
        encoded, signature = cursor.split(".")
        expected = hmac.new(
            settings.jwt_secret_key.encode(),
            ("admin-cursor:" + encoded).encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        value = json.loads(
            base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
        )
        date = datetime.fromisoformat(value["at"])
        if date.tzinfo is None or value["filters"] != cursor_fingerprint(filters):
            raise ValueError
        return date.astimezone(UTC), ObjectId(value["id"])
    except (ValueError, TypeError, KeyError, InvalidId):
        raise fail(422, "CURSOR_INVALID", "Invalid cursor or changed filters") from None


async def list_users(db, actor, limit, cursor, filters, request_id):
    query = dict(filters)
    if cursor:
        date, user_id = decode_cursor(cursor, filters)
        query["$or"] = [
            {"created_at": {"$lt": date}},
            {"created_at": date, "_id": {"$lt": user_id}},
        ]
    # Exclude secret fields before they leave the database, not just in the response.
    rows = (
        await db.users.find(query, {"password_hash": 0})
        .sort([("created_at", -1), ("_id", -1)])
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    page = rows[:limit]
    next_cursor = encode_cursor(page[-1], filters) if len(rows) > limit else None
    await audit_admin(db, actor["_id"], "admin.users_listed", "SUCCESS", request_id)
    return {"users": [public_user(user) for user in page], "next_cursor": next_cursor}
