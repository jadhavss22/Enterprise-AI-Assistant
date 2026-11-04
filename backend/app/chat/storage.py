from pymongo import ASCENDING, DESCENDING


async def ensure_chat_storage(db):
    await db.conversations.create_index([("owner_id", ASCENDING), ("_id", DESCENDING)])
    await db.chat_turns.create_index(
        [("conversation_id", ASCENDING), ("client_request_id", ASCENDING)], unique=True
    )
    await db.chat_turns.create_index(
        [("conversation_id", ASCENDING), ("_id", DESCENDING)]
    )
