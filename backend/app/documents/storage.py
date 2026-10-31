from pymongo import ASCENDING, DESCENDING


async def ensure_document_storage(db) -> None:
    await db.documents.create_index(
        [("uploaded_by", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$exists": True}},
    )
    await db.documents.create_index(
        [("sha256", ASCENDING), ("duplicate_scope_key", ASCENDING)],
        unique=True,
        # MongoDB partial indexes do not support ``$exists: false``. Equality
        # with null covers both absent and explicitly-null deletion markers,
        # while deleted documents contain a timestamp and leave this index.
        partialFilterExpression={"deleted_at": None},
    )
    await db.documents.create_index(
        [("uploaded_by", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)]
    )
    await db.documents.create_index(
        [
            ("department_id", ASCENDING),
            ("visibility", ASCENDING),
            ("created_at", DESCENDING),
        ]
    )
    await db.documents.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    await db.ingestion_jobs.create_index(
        "document_id",
        unique=True,
        partialFilterExpression={"status": {"$in": ["QUEUED", "PROCESSING"]}},
        name="one_active_ingestion_job_per_document",
    )
    await db.ingestion_jobs.create_index(
        [("status", ASCENDING), ("next_retry_at", ASCENDING)]
    )
    await db.ingestion_jobs.create_index(
        [("dispatch_status", ASCENDING), ("created_at", ASCENDING)]
    )
    await db.ingestion_jobs.create_index("lease_expires_at")
    await db.ingestion_jobs.create_index(
        [("document_id", ASCENDING), ("created_at", DESCENDING)]
    )
