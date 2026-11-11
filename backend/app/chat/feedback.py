from typing import Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from app.auth.dependencies import current_user, get_db, get_redis
from app.auth.models import AuditRecord, utcnow
from app.auth.rate_limits import rate_limit
from app.chat import service
from app.documents.validation import fail

router = APIRouter(prefix="/api/v1/messages", tags=["feedback"])


class FeedbackWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    rating: Literal["THUMBS_UP", "THUMBS_DOWN"]
    comment: str | None = Field(default=None, max_length=1000)


def public_feedback(row):
    return {key: row[key] for key in ("rating", "comment", "created_at", "updated_at")}


@router.post("/{message_id}/feedback", status_code=200)
async def save_feedback(
    message_id: str,
    payload: FeedbackWrite,
    request: Request,
    response: Response,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(redis, f"feedback:{actor['_id']}", 30, 60)
    if not ObjectId.is_valid(message_id):
        raise fail(404, "ANSWER_NOT_FOUND", "Answer was not found")
    message_id = ObjectId(message_id)
    comment = (payload.comment or None) if payload.rating == "THUMBS_DOWN" else None

    async def operation(session):
        turn = await db.chat_turns.find_one({"_id": message_id}, session=session)
        if not turn:
            raise fail(404, "ANSWER_NOT_FOUND", "Answer was not found")
        cid = str(turn["conversation_id"])
        await service.owned(db, cid, actor, session)
        if turn["status"] != "COMPLETED":
            raise fail(409, "ANSWER_INCOMPLETE", "Only completed answers can be rated")
        # Serialize feedback with conversation deletion.
        await db.conversations.update_one(
            service.owner_query(cid, actor), {"$inc": {"revision": 1}}, session=session
        )
        await service.check_sources(db, actor, turn.get("manifest", []))
        query = {"message_id": message_id, "user_id": actor["_id"]}
        previous = await db.feedback.find_one(query, session=session)
        if previous and previous["rating"] == payload.rating and previous["comment"] == comment:
            return previous, False
        now = utcnow()
        row = await db.feedback.find_one_and_update(
            query,
            {"$set": {"rating": payload.rating, "comment": comment, "updated_at": now},
             "$setOnInsert": {"created_at": now, "conversation_id": turn["conversation_id"]}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        await db.audit_logs.insert_one(
            AuditRecord(
                actor_id=actor["_id"],
                event="chat.feedback.save",
                resource_type="chat_turn",
                resource_id=message_id,
                request_id=request.state.request_id,
                outcome="ALLOWED",
                metadata={"rating": payload.rating},
            ).bson(),
            session=session,
        )
        return row, previous is None

    row, created = await service.transaction(db, operation)
    response.status_code = 201 if created else 200
    return public_feedback(row)
