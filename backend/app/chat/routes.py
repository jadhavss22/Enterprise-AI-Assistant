import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.dependencies import current_user, get_db, get_redis
from app.auth.rate_limits import rate_limit
from app.chat import service
from app.chat.schemas import ChatRequest, ConversationWrite
from app.config import settings

router = APIRouter(prefix="/api/v1/conversations", tags=["chat"])

generation_slots = asyncio.Semaphore(2)


class GenerationResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            generation_slots.release()


@router.post("", status_code=201)
async def create(
    payload: ConversationWrite,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(redis, f"chat-create:{actor['_id']}", 10, 60)
    return await service.create_conversation(
        db, actor, payload.title, request.state.request_id
    )


@router.get("")
async def listing(
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
):
    await rate_limit(redis, f"chat-read:{actor['_id']}", 120, 60)
    query = {"owner_id": actor["_id"], "deleted_at": None}
    if cursor:
        query["_id"] = {"$lt": service.oid(cursor)}
    rows = (
        await db.conversations.find(query)
        .sort("_id", -1)
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    return {
        "conversations": [service.public_conversation(row) for row in rows[:limit]],
        "next_cursor": str(rows[limit - 1]["_id"]) if len(rows) > limit else None,
    }


@router.patch("/{cid}")
async def rename(
    cid: str,
    payload: ConversationWrite,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(redis, f"chat-write:{actor['_id']}", 30, 60)
    await service.mutate_conversation(db, cid, actor, title=payload.title)
    return service.public_conversation(await service.owned(db, cid, actor))


@router.delete("/{cid}", status_code=204)
async def delete(
    cid: str, actor=Depends(current_user), db=Depends(get_db), redis=Depends(get_redis)
):
    await rate_limit(redis, f"chat-write:{actor['_id']}", 30, 60)
    await service.mutate_conversation(db, cid, actor, delete=True)
    return Response(status_code=204)


@router.get("/{cid}/messages")
async def messages(
    cid: str,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
):
    await rate_limit(redis, f"chat-read:{actor['_id']}", 120, 60)
    await service.owned(db, cid, actor)
    query = {"conversation_id": service.oid(cid)}
    if cursor:
        query["_id"] = {"$lt": service.oid(cursor)}
    rows = (
        await db.chat_turns.find(query)
        .sort("_id", -1)
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    turns = [await service.public_turn(db, actor, turn) for turn in rows[:limit]]
    # Conversation deletion during the ACL reads must not disclose its messages.
    await service.owned(db, cid, actor)
    await service.revalidate_public_turns(db, actor, rows[:limit], turns)
    return {
        "turns": list(reversed(turns)),
        "next_cursor": str(rows[limit - 1]["_id"]) if len(rows) > limit else None,
    }


@router.post("/{cid}/messages/stream")
async def stream(
    cid: str,
    payload: ChatRequest,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(
        redis, f"chat-generate:{actor['_id']}", settings.chat_rate_limit, 60
    )
    try:
        await asyncio.wait_for(generation_slots.acquire(), 0.1)
    except TimeoutError:
        from app.documents.validation import fail

        raise fail(503, "CHAT_BUSY", "Chat capacity is busy; retry shortly") from None
    try:
        turn, replay = await service.acquire(db, cid, actor, payload)
        return GenerationResponse(
            service.generate(
                db,
                cid,
                actor,
                turn,
                replay,
                request.state.request_id,
                request.is_disconnected,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    except BaseException:
        generation_slots.release()
        raise


@router.post("/{cid}/generations/{key}/cancel", status_code=204)
async def cancel(
    cid: str,
    key: UUID,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(redis, f"chat-cancel:{actor['_id']}", 30, 60)
    await service.cancel_turn(db, cid, actor, key)
    return Response(status_code=204)
