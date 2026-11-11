from pymongo import ASCENDING, DESCENDING


async def ensure_chat_storage(db):
    await db.feedback.create_index([("message_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    await db.feedback.create_index("created_at")
    await db.feedback.create_index("conversation_id")
    await db.conversations.create_index([("owner_id", ASCENDING), ("_id", DESCENDING)])
    await db.chat_turns.create_index(
        [("conversation_id", ASCENDING), ("client_request_id", ASCENDING)], unique=True
    )
    await db.chat_turns.create_index(
        [("conversation_id", ASCENDING), ("_id", DESCENDING)]
    )
