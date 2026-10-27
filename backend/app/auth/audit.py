"""Security events accept identifiers and fixed event names, never request bodies."""

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from app.auth.models import AuditRecord


async def record_audit(
    db, event, outcome, *, user_id=None, session_id=None, request_id="internal"
):
    record = AuditRecord(
        actor_id=user_id,
        event=event,
        resource_type="auth_session",
        resource_id=session_id,
        request_id=request_id,
        outcome=outcome,
    ).bson()
    try:
        await db.audit_logs.insert_one(record)
    except PyMongoError:
        raise HTTPException(
            503,
            detail={
                "code": "AUDIT_UNAVAILABLE",
                "message": "Security audit unavailable",
            },
        ) from None
