import asyncio
import base64
import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.auth.models import AuditRecord, Role, utcnow
from app.auth.service import session_matches_user
from app.documents.access import (
    can_manage_document,
    document_object_id,
    require_visible_document,
    visible_documents_query,
)
from app.documents.blob_store import blob_container, delete_blob, upload_private_blob
from app.documents.models import (
    DocumentStatus,
    DocumentVisibility,
    IngestionStage,
    IngestionStatus,
)
from app.documents.validation import fail

logger = logging.getLogger(__name__)


def serialize_document(row: dict, job: dict | None = None) -> dict:
    return {
        "document_id": str(row["_id"]),
        "filename": row["filename"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "uploaded_by": str(row["uploaded_by"]),
        "department_id": str(row["department_id"]),
        "visibility": row["visibility"],
        "status": row["status"],
        "active_index_version": row.get("active_index_version"),
        "chunk_count": row.get("chunk_count", 0),
        "ingestion_status": job.get("status") if job else None,
        "ingestion_stage": job.get("stage") if job else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def serialize_job(row: dict) -> dict:
    return {
        "job_id": str(row["_id"]),
        "document_id": str(row["document_id"]),
        "status": row["status"],
        "stage": row.get("stage"),
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "index_cleanup_status": row.get("index_cleanup_status"),
        "index_cleanup_error_code": row.get("index_cleanup_error_code"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def duplicate_scope_key(visibility: DocumentVisibility, actor: dict) -> str:
    if visibility == DocumentVisibility.PRIVATE:
        return f"PRIVATE:{actor['_id']}"
    if visibility == DocumentVisibility.DEPARTMENT:
        return f"DEPARTMENT:{actor['department_id']}"
    return "ORGANIZATION"


def validate_upload_role(actor: dict, visibility: DocumentVisibility) -> None:
    role = actor["role"]
    if role not in {Role.MANAGER.value, Role.ADMIN.value}:
        raise fail(403, "UPLOAD_FORBIDDEN", "Your role cannot upload documents")
    if visibility == DocumentVisibility.ORGANIZATION and role != Role.ADMIN.value:
        raise fail(
            403,
            "ORGANIZATION_PUBLISH_FORBIDDEN",
            "Only Admin can publish organization documents",
        )


async def create_document(
    db,
    redis,
    actor,
    filename,
    mime_type,
    content,
    visibility,
    request_id,
    idempotency_key,
):
    validate_upload_role(actor, visibility)
    from hashlib import sha256

    digest = sha256(content).hexdigest()
    scope = duplicate_scope_key(visibility, actor)
    prior = await db.documents.find_one(
        {"uploaded_by": actor["_id"], "idempotency_key": idempotency_key}
    )
    if prior:
        if prior["sha256"] != digest:
            raise fail(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "This upload request key was already used",
            )
        prior_job = await db.ingestion_jobs.find_one(
            {"document_id": prior["_id"]}, sort=[("created_at", -1)]
        )
        if prior_job:
            return {
                "document_id": str(prior["_id"]),
                "job_id": str(prior_job["_id"]),
                "status": prior["status"],
            }
    existing = await db.documents.find_one(
        {
            "sha256": digest,
            "duplicate_scope_key": scope,
            "deleted_at": {"$exists": False},
        },
        {"_id": 1},
    )
    if existing:
        raise fail(
            409,
            "DUPLICATE_DOCUMENT",
            "This file already exists in the selected visibility scope",
        )

    now = utcnow()
    document_id, job_id = ObjectId(), ObjectId()
    blob_key = f"{document_id}/{uuid4().hex}"
    document = {
        "_id": document_id,
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": len(content),
        "sha256": digest,
        "duplicate_scope_key": scope,
        "idempotency_key": idempotency_key,
        "uploaded_by": actor["_id"],
        "department_id": actor["department_id"],
        "visibility": visibility.value,
        "allowed_user_ids": [],
        "blob_key": blob_key,
        "status": DocumentStatus.QUEUED.value,
        "active_index_version": None,
        "chunk_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    job = {
        "_id": job_id,
        "document_id": document_id,
        "target_index_version": 1,
        "status": IngestionStatus.QUEUED.value,
        "stage": IngestionStage.VALIDATING.value,
        "attempt_count": 0,
        "max_attempts": 3,
        "dispatch_status": "PENDING",
        "fencing_token": 0,
        "created_at": now,
        "updated_at": now,
    }
    audit = AuditRecord(
        actor_id=actor["_id"],
        event="document.uploaded",
        resource_type="document",
        resource_id=document_id,
        request_id=request_id,
        outcome="SUCCESS",
        metadata={"visibility": visibility.value, "size_bytes": len(content)},
    ).bson()

    await upload_private_blob(blob_key, content, mime_type)
    try:
        async with await db.client.start_session() as session:

            async def persist(tx):
                await db.documents.insert_one(document, session=tx)
                await db.ingestion_jobs.insert_one(job, session=tx)
                await db.audit_logs.insert_one(audit, session=tx)

            await session.with_transaction(persist)
    except DuplicateKeyError:
        await delete_blob(blob_key)
        prior = await db.documents.find_one(
            {"uploaded_by": actor["_id"], "idempotency_key": idempotency_key}
        )
        if prior and prior.get("sha256") == digest:
            prior_job = await db.ingestion_jobs.find_one(
                {"document_id": prior["_id"]}, sort=[("created_at", -1)]
            )
            if prior_job:
                return {
                    "document_id": str(prior["_id"]),
                    "job_id": str(prior_job["_id"]),
                    "status": prior["status"],
                }
        raise fail(
            409,
            "DUPLICATE_DOCUMENT",
            "This file already exists in the selected visibility scope",
        ) from None
    except Exception:
        await delete_blob(blob_key)
        raise

    try:
        from app.ingestion.tasks import process_ingestion_job

        await asyncio.to_thread(process_ingestion_job.delay, str(job_id))
        await db.ingestion_jobs.update_one(
            {"_id": job_id, "dispatch_status": "PENDING"},
            {
                "$set": {
                    "dispatch_status": "DISPATCHED",
                    "dispatched_at": utcnow(),
                    "updated_at": utcnow(),
                }
            },
        )
    except Exception:
        # MongoDB remains the job authority. A later recovery pass can dispatch PENDING.
        logger.exception("Initial ingestion dispatch failed; reconciliation will retry")
    return {
        "document_id": str(document_id),
        "job_id": str(job_id),
        "status": DocumentStatus.QUEUED.value,
    }


def decode_cursor(cursor: str | None):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        return datetime.fromisoformat(value["created_at"]), ObjectId(value["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise fail(422, "CURSOR_INVALID", "Invalid document cursor") from None


def encode_cursor(row: dict) -> str:
    value = {
        "created_at": row["created_at"].astimezone(UTC).isoformat(),
        "id": str(row["_id"]),
    }
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


async def list_documents(
    db, actor, limit: int, cursor: str | None, request_id: str = "internal"
):
    query = visible_documents_query(actor)
    position = decode_cursor(cursor)
    if position:
        created_at, object_id = position
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"created_at": {"$lt": created_at}},
                        {"created_at": created_at, "_id": {"$lt": object_id}},
                    ]
                },
            ]
        }
    rows = (
        await db.documents.find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    latest_jobs = {}
    if rows:
        job_rows = await db.ingestion_jobs.aggregate(
            [
                {"$match": {"document_id": {"$in": [row["_id"] for row in rows]}}},
                {"$sort": {"created_at": -1}},
                {
                    "$group": {
                        "_id": "$document_id",
                        "status": {"$first": "$status"},
                        "stage": {"$first": "$stage"},
                    }
                },
            ]
        ).to_list(length=len(rows))
        for job in job_rows:
            latest_jobs[job["_id"]] = job
    if actor["role"] == Role.ADMIN.value:
        for row in rows:
            await audit_admin_private_access(db, actor, row, request_id)
    return {
        "documents": [
            serialize_document(row, latest_jobs.get(row["_id"])) for row in rows
        ],
        "next_cursor": encode_cursor(rows[-1]) if has_more and rows else None,
    }


async def get_visible_document(db, actor, document_id: str):
    row = await db.documents.find_one({"_id": document_object_id(document_id)})
    return require_visible_document(actor, row)


async def audit_admin_private_access(db, actor, document, request_id):
    if (
        actor["role"] != Role.ADMIN.value
        or document["visibility"] != DocumentVisibility.PRIVATE.value
    ):
        return
    row = AuditRecord(
        actor_id=actor["_id"],
        event="document.admin_private_access",
        resource_type="document",
        resource_id=document["_id"],
        request_id=request_id,
        outcome="SUCCESS",
        metadata={"access": "metadata_or_source"},
    ).bson()
    await db.audit_logs.insert_one(row)


async def require_document_manager(actor, document):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    return document


async def stream_document_source(blob_key: str):
    async with blob_container() as container:
        blob = container.get_blob_client(blob_key)
        response = await blob.download_blob()
        async for chunk in response.chunks():
            yield chunk


async def list_document_jobs(db, actor, document):
    await require_document_manager(actor, document)
    rows = (
        await db.ingestion_jobs.find({"document_id": document["_id"]})
        .sort("created_at", -1)
        .limit(20)
        .to_list(length=20)
    )
    return [serialize_job(row) for row in rows]


async def get_ingestion_job(db, actor, job_id: str):
    try:
        oid = ObjectId(job_id)
    except (ValueError, TypeError):
        raise fail(422, "JOB_ID_INVALID", "Invalid ingestion job ID") from None
    job = await db.ingestion_jobs.find_one({"_id": oid})
    if job is None:
        raise fail(404, "JOB_NOT_FOUND", "Ingestion job was not found")
    document = await get_visible_document(db, actor, str(job["document_id"]))
    await require_document_manager(actor, document)
    return serialize_job(job)


async def _assert_current_document_actor(db, actor, tx):
    current = await db.users.find_one(
        {"_id": actor["_id"], "status": "ACTIVE", "role": actor["role"]},
        session=tx,
    )
    session = await db.auth_sessions.find_one(
        {"_id": actor["_auth_session"]["_id"]},
        session=tx,
    )
    if not session_matches_user(session, current):
        raise fail(401, "SESSION_INVALID", "Your session is no longer active")


async def get_document_permissions(actor, document):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    return {
        "visibility": document["visibility"],
        "allowed_user_ids": [
            str(value) for value in document.get("allowed_user_ids", [])
        ],
    }


async def list_document_share_targets(db, actor, document):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    rows = (
        await db.users.find(
            {
                "department_id": document["department_id"],
                "status": "ACTIVE",
                "_id": {"$ne": document["uploaded_by"]},
            },
            {"_id": 1, "name": 1, "role": 1},
        )
        .sort([("name", 1), ("_id", 1)])
        .limit(200)
        .to_list(length=200)
    )
    return {
        "users": [
            {
                "user_id": str(row["_id"]),
                "display_name": row["name"],
                "role": row["role"],
            }
            for row in rows
        ]
    }


async def update_document_permissions(db, actor, document, payload, request_id):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    now = utcnow()
    async with await db.client.start_session() as session:

        async def transaction(tx):
            await _assert_current_document_actor(db, actor, tx)
            current_doc = await db.documents.find_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                session=tx,
            )
            if not current_doc or not can_manage_document(actor, current_doc):
                raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
            visibility = payload.visibility or DocumentVisibility(
                current_doc["visibility"]
            )
            allowed = (
                payload.allowed_user_ids
                if payload.allowed_user_ids is not None
                else current_doc.get("allowed_user_ids", [])
            )
            if (
                visibility == DocumentVisibility.ORGANIZATION
                and actor["role"] != Role.ADMIN.value
            ):
                raise fail(
                    403,
                    "ORGANIZATION_PUBLISH_FORBIDDEN",
                    "Only Admin can publish organization documents",
                )
            if visibility != DocumentVisibility.PRIVATE:
                allowed = []
            if allowed:
                valid = await db.users.count_documents(
                    {
                        "_id": {"$in": [ObjectId(item) for item in allowed]},
                        "status": "ACTIVE",
                        "department_id": current_doc["department_id"],
                    },
                    session=tx,
                )
                if valid != len(allowed):
                    raise fail(
                        422,
                        "SHARE_TARGET_INVALID",
                        "Private sharing is limited to active users in the uploader's department",
                    )
            scope = duplicate_scope_key(
                visibility,
                {
                    "_id": current_doc["uploaded_by"],
                    "department_id": current_doc["department_id"],
                },
            )
            duplicate = await db.documents.find_one(
                {
                    "_id": {"$ne": current_doc["_id"]},
                    "sha256": current_doc["sha256"],
                    "duplicate_scope_key": scope,
                    "deleted_at": {"$exists": False},
                },
                {"_id": 1},
                session=tx,
            )
            if duplicate:
                raise fail(
                    409,
                    "DUPLICATE_DOCUMENT",
                    "This file already exists in the selected visibility scope",
                )
            await db.documents.update_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                {
                    "$set": {
                        "visibility": visibility.value,
                        "allowed_user_ids": [ObjectId(item) for item in allowed],
                        "duplicate_scope_key": scope,
                        "updated_at": now,
                    }
                },
                session=tx,
            )
            audit = AuditRecord(
                actor_id=actor["_id"],
                event="document.permissions_changed",
                resource_type="document",
                resource_id=document["_id"],
                request_id=request_id,
                outcome="SUCCESS",
                metadata={
                    "fields": sorted(payload.model_fields_set),
                    "visibility": visibility.value,
                    "authorized_user_count": len(allowed),
                },
            ).bson()
            await db.audit_logs.insert_one(audit, session=tx)

        try:
            await session.with_transaction(transaction)
        except DuplicateKeyError:
            raise fail(
                409,
                "DUPLICATE_DOCUMENT",
                "This file already exists in the selected visibility scope",
            ) from None
    return await get_visible_document(db, actor, str(document["_id"]))


async def soft_delete_document(db, actor, document, request_id):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    now = utcnow()
    async with await db.client.start_session() as session:

        async def transaction(tx):
            await _assert_current_document_actor(db, actor, tx)
            current_doc = await db.documents.find_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                session=tx,
            )
            if not current_doc or not can_manage_document(actor, current_doc):
                raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
            await db.documents.update_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                {
                    "$set": {
                        "status": DocumentStatus.DELETED.value,
                        "deleted_at": now,
                        "cleanup_dispatch_status": "PENDING",
                        "cleanup_attempt_count": 0,
                        "updated_at": now,
                    }
                },
                session=tx,
            )
            await db.ingestion_jobs.update_many(
                {
                    "document_id": document["_id"],
                    "status": {"$in": ["QUEUED", "PROCESSING"]},
                },
                {
                    "$set": {
                        "status": "FAILED",
                        "error_code": "DOCUMENT_DELETED",
                        "error_message": "The document is no longer available.",
                        "updated_at": now,
                    },
                    "$inc": {"fencing_token": 1},
                    "$unset": {"lease_owner": "", "lease_expires_at": ""},
                },
                session=tx,
            )
            await db.audit_logs.insert_one(
                AuditRecord(
                    actor_id=actor["_id"],
                    event="document.deleted",
                    resource_type="document",
                    resource_id=document["_id"],
                    request_id=request_id,
                    outcome="SUCCESS",
                    metadata={},
                ).bson(),
                session=tx,
            )

        await session.with_transaction(transaction)
    try:
        from app.ingestion.tasks import cleanup_deleted_document

        await asyncio.to_thread(cleanup_deleted_document.delay, str(document["_id"]))
        await db.documents.update_one(
            {"_id": document["_id"], "cleanup_dispatch_status": "PENDING"},
            {
                "$set": {
                    "cleanup_dispatch_status": "DISPATCHED",
                    "cleanup_dispatched_at": utcnow(),
                }
            },
        )
    except Exception:
        logger.exception("Document cleanup dispatch failed; reconciliation will retry")
        await db.documents.update_one(
            {"_id": document["_id"]}, {"$set": {"cleanup_dispatch_status": "PENDING"}}
        )


async def create_reprocess_job(db, actor, document, request_id, *, admin_retry=False):
    if not can_manage_document(actor, document):
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    if document.get("deleted_at") is not None:
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    in_flight = await db.ingestion_jobs.find_one(
        {"document_id": document["_id"], "status": {"$in": ["QUEUED", "PROCESSING"]}}
    )
    if in_flight:
        raise fail(
            409, "INGESTION_ALREADY_RUNNING", "An ingestion job is already running"
        )
    latest = await db.ingestion_jobs.find_one(
        {"document_id": document["_id"]}, sort=[("target_index_version", -1)]
    )
    target_version = (
        max(
            document.get("active_index_version") or 0,
            latest.get("target_index_version", 0) if latest else 0,
        )
        + 1
    )
    now, job_id = utcnow(), ObjectId()
    job = {
        "_id": job_id,
        "document_id": document["_id"],
        "target_index_version": target_version,
        "status": "QUEUED",
        "stage": IngestionStage.VALIDATING.value,
        "attempt_count": 0,
        "max_attempts": 3,
        "dispatch_status": "PENDING",
        "fencing_token": 0,
        "created_at": now,
        "updated_at": now,
    }
    async with await db.client.start_session() as session:

        async def transaction(tx):
            await _assert_current_document_actor(db, actor, tx)
            current_doc = await db.documents.find_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}}, session=tx
            )
            if not current_doc or not can_manage_document(actor, current_doc):
                raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
            current_latest = await db.ingestion_jobs.find_one(
                {"document_id": document["_id"]},
                sort=[("target_index_version", -1)],
                session=tx,
            )
            job["target_index_version"] = (
                max(
                    current_doc.get("active_index_version") or 0,
                    current_latest.get("target_index_version", 0)
                    if current_latest
                    else 0,
                )
                + 1
            )
            await db.ingestion_jobs.insert_one(job, session=tx)
            if current_doc.get("active_index_version") is None:
                await db.documents.update_one(
                    {"_id": document["_id"], "deleted_at": {"$exists": False}},
                    {"$set": {"status": "QUEUED", "updated_at": now}},
                    session=tx,
                )
            event = (
                "admin.ingestion_retry"
                if admin_retry
                else "document.reprocess_requested"
            )
            await db.audit_logs.insert_one(
                AuditRecord(
                    actor_id=actor["_id"],
                    event=event,
                    resource_type="document",
                    resource_id=document["_id"],
                    request_id=request_id,
                    outcome="SUCCESS",
                    metadata={"target_index_version": job["target_index_version"]},
                ).bson(),
                session=tx,
            )

        try:
            await session.with_transaction(transaction)
        except DuplicateKeyError:
            raise fail(
                409, "INGESTION_ALREADY_RUNNING", "An ingestion job is already running"
            ) from None
    try:
        from app.ingestion.tasks import process_ingestion_job

        result = await asyncio.to_thread(process_ingestion_job.delay, str(job_id))
        await db.ingestion_jobs.update_one(
            {"_id": job_id, "dispatch_status": "PENDING"},
            {
                "$set": {
                    "dispatch_status": "DISPATCHED",
                    "dispatch_task_id": result.id,
                    "dispatched_at": utcnow(),
                    "updated_at": utcnow(),
                }
            },
        )
    except Exception:
        logger.exception("Reprocess dispatch failed; reconciliation will retry")
    return {
        "document_id": str(document["_id"]),
        "job_id": str(job_id),
        "status": "QUEUED",
    }


async def admin_retry_job(db, actor, job_id, request_id):
    try:
        oid = ObjectId(job_id)
    except (ValueError, TypeError):
        raise fail(422, "JOB_ID_INVALID", "Invalid ingestion job ID") from None
    prior = await db.ingestion_jobs.find_one({"_id": oid, "status": "FAILED"})
    if prior is None:
        raise fail(404, "JOB_NOT_FOUND", "Failed ingestion job was not found")
    document = await db.documents.find_one(
        {"_id": prior["document_id"], "deleted_at": {"$exists": False}}
    )
    if document is None:
        raise fail(404, "DOCUMENT_NOT_FOUND", "Document was not found")
    return await create_reprocess_job(db, actor, document, request_id, admin_retry=True)


async def list_admin_ingestion_jobs(
    db, redis, actor, status, limit, cursor, request_id
):
    from app.auth.rate_limits import rate_limit
    from app.config import settings

    await rate_limit(
        redis,
        f"admin-ingestion-jobs:{actor['_id']}",
        settings.admin_read_rate_limit,
        60,
    )
    query = {} if status is None else {"status": status.value}
    position = decode_cursor(cursor)
    if position:
        created_at, object_id = position
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"created_at": {"$lt": created_at}},
                        {"created_at": created_at, "_id": {"$lt": object_id}},
                    ]
                },
            ]
        }
    rows = (
        await db.ingestion_jobs.find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    await db.audit_logs.insert_one(
        AuditRecord(
            actor_id=actor["_id"],
            event="admin.ingestion_jobs_viewed",
            resource_type="ingestion_jobs",
            request_id=request_id,
            outcome="SUCCESS",
            metadata={
                "status_filter": status.value if status else None,
                "returned_count": len(rows),
            },
        ).bson()
    )
    return {
        "jobs": [serialize_job(row) for row in rows],
        "next_cursor": encode_cursor(rows[-1]) if has_more and rows else None,
    }
