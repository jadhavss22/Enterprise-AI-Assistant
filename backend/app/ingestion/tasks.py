import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

from azure.storage.blob import BlobServiceClient
from bson import ObjectId
from celery.exceptions import Ignore
from pymongo import MongoClient, ReturnDocument

from app.auth.models import AuditRecord
from app.config import settings
from app.documents.models import IngestionStage
from app.ingestion.chunking import chunk_pages
from app.ingestion.extraction import extract_pages
from app.ingestion.providers import embedding_provider, pinecone_index
from app.workers.celery_app import celery_app

_AZURITE_CONNECTION = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1;"
)
_LEASE = timedelta(minutes=60)
logger = logging.getLogger(__name__)
_DISPATCH_TIMEOUT = timedelta(minutes=5)


def _begin_index_write(db, client, job, document):
    """Make an external write visible to deletion cleanup before it starts."""
    now = datetime.now(UTC)
    with client.start_session() as session:

        def transaction(tx):
            current = db.ingestion_jobs.find_one(
                {
                    "_id": job["_id"],
                    "status": "PROCESSING",
                    "fencing_token": job["fencing_token"],
                },
                session=tx,
            )
            if current is None:
                raise RuntimeError("INGESTION_LEASE_LOST")
            # This document write serializes the intent against soft deletion.
            available = db.documents.update_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                {"$inc": {"index_write_revision": 1}},
                session=tx,
            )
            if available.matched_count != 1:
                raise RuntimeError("INGESTION_LEASE_LOST")
            db.ingestion_jobs.update_one(
                {"_id": job["_id"], "fencing_token": job["fencing_token"]},
                {
                    "$set": {
                        "vector_write_pending": True,
                        "vector_write_token": job["fencing_token"],
                        "vector_write_expires_at": now + _LEASE,
                    }
                },
                session=tx,
            )

        session.with_transaction(transaction)


def _renew_index_write(db, job):
    _renew_lease(db, job)
    result = db.ingestion_jobs.update_one(
        {
            "_id": job["_id"],
            "vector_write_pending": True,
            "vector_write_token": job["fencing_token"],
        },
        {"$set": {"vector_write_expires_at": datetime.now(UTC) + _LEASE}},
    )
    if result.matched_count != 1:
        raise RuntimeError("INGESTION_LEASE_LOST")


def _finish_index_write(db, job, document):
    db.ingestion_jobs.update_one(
        {"_id": job["_id"], "vector_write_token": job["fencing_token"]},
        {
            "$set": {"vector_write_pending": False},
            "$unset": {"vector_write_expires_at": ""},
        },
    )
    # A late completion after a reclaimed lease must reopen cleanup even if a
    # prior cleanup already succeeded. MongoDB retains this intent if Redis is down.
    db.documents.update_one(
        {"_id": document["_id"], "deleted_at": {"$exists": True}},
        {
            "$set": {
                "cleanup_dispatch_status": "PENDING",
                "cleanup_attempt_count": 0,
                "updated_at": datetime.now(UTC),
            },
            "$inc": {"cleanup_fencing_token": 1},
        },
    )


def _is_not_found(error):
    status = getattr(error, "status", None) or getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if response is not None:
        status = status or getattr(response, "status_code", None)
    return status == 404 or error.__class__.__name__.lower() in {
        "notfounderror",
        "notfoundexception",
    }


def _renew_lease(db, job):
    now = datetime.now(UTC)
    result = db.ingestion_jobs.update_one(
        {
            "_id": job["_id"],
            "fencing_token": job["fencing_token"],
            "status": "PROCESSING",
        },
        {"$set": {"lease_expires_at": now + _LEASE, "updated_at": now}},
    )
    if result.matched_count != 1:
        raise RuntimeError("INGESTION_LEASE_LOST")


def _stage(db, job, stage):
    _renew_lease(db, job)
    result = db.ingestion_jobs.update_one(
        {
            "_id": job["_id"],
            "fencing_token": job["fencing_token"],
            "status": "PROCESSING",
        },
        {"$set": {"stage": stage.value, "updated_at": datetime.now(UTC)}},
    )
    if result.matched_count != 1:
        raise RuntimeError("INGESTION_LEASE_LOST")


def _embed_chunks(db, job, chunks, provider):
    vectors = []
    for start in range(0, len(chunks), provider.batch_size):
        _renew_lease(db, job)
        batch = chunks[start : start + provider.batch_size]
        vectors.extend(provider.embed([chunk.text for chunk in batch]))
    if len(vectors) != len(chunks) or any(
        len(vector) != provider.dimension for vector in vectors
    ):
        raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
    return vectors


def _upsert_and_verify(
    index,
    document,
    target_version,
    chunks,
    embeddings,
    expected_dimension,
    before_batch=None,
):
    namespace = f"{document['_id']}-v{target_version}"
    stats = index.describe_index_stats(timeout=60)
    actual_dimension = (
        stats.get("dimension")
        if isinstance(stats, dict)
        else getattr(stats, "dimension", None)
    )
    if actual_dimension is not None and actual_dimension != expected_dimension:
        raise RuntimeError("EMBEDDING_DIMENSION_MISMATCH")
    vectors = []
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        metadata = {
            "document_id": str(document["_id"]),
            "index_version": target_version,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
        }
        if chunk.page is not None:
            metadata["page"] = chunk.page
        vector_id = hashlib.sha256(
            f"{document['_id']}:{target_version}:{chunk.chunk_index}".encode()
        ).hexdigest()
        vectors.append((vector_id, embedding, metadata))
    for start in range(0, len(vectors), 100):
        if before_batch is not None:
            before_batch()
        index.upsert(
            vectors=vectors[start : start + 100], namespace=namespace, timeout=60
        )
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        stats = index.describe_index_stats(timeout=60)
        namespaces = getattr(stats, "namespaces", {}) or {}
        current = namespaces.get(namespace)
        count = getattr(current, "vector_count", 0) if current else 0
        if count == len(chunks):
            return namespace
        if count > len(chunks):
            raise RuntimeError("INDEX_VERSION_COUNT_MISMATCH")
        time.sleep(2)
    raise RuntimeError("INDEX_VERSION_NOT_VERIFIED")


def _activate(db, client, job, document, namespace, chunk_count):
    now = datetime.now(UTC)
    with client.start_session() as session:

        def transaction(tx):
            current_job = db.ingestion_jobs.find_one(
                {
                    "_id": job["_id"],
                    "status": "PROCESSING",
                    "fencing_token": job["fencing_token"],
                },
                session=tx,
            )
            current_document = db.documents.find_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                session=tx,
            )
            if not current_job or not current_document:
                raise RuntimeError("INGESTION_LEASE_LOST")
            previous_version = current_document.get("active_index_version")
            result = db.documents.update_one(
                {"_id": document["_id"], "deleted_at": {"$exists": False}},
                {
                    "$set": {
                        "status": "READY",
                        "active_index_version": job["target_index_version"],
                        "chunk_count": chunk_count,
                        "updated_at": now,
                    }
                },
                session=tx,
            )
            if result.modified_count != 1:
                raise RuntimeError("DOCUMENT_STATE_CHANGED")
            db.ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "status": "PROCESSING",
                    "fencing_token": job["fencing_token"],
                },
                {
                    "$set": {
                        "status": "SUCCEEDED",
                        "stage": IngestionStage.COMPLETE.value,
                        "updated_at": now,
                        "verified_namespace": namespace,
                        "previous_index_version": previous_version,
                        "index_cleanup_status": "PENDING"
                        if previous_version is not None
                        and previous_version != job["target_index_version"]
                        else "NOT_REQUIRED",
                        "index_cleanup_attempt_count": 0,
                    },
                    "$unset": {
                        "lease_owner": "",
                        "lease_expires_at": "",
                        "next_retry_at": "",
                        "error_code": "",
                        "error_message": "",
                    },
                },
                session=tx,
            )
            db.audit_logs.insert_one(
                AuditRecord(
                    actor_id=document["uploaded_by"],
                    event="ingestion.completed",
                    resource_type="document",
                    resource_id=document["_id"],
                    request_id=str(job["_id"]),
                    outcome="SUCCESS",
                    metadata={
                        "chunk_count": chunk_count,
                        "index_version": job["target_index_version"],
                    },
                ).bson(),
                session=tx,
            )
            return previous_version

        return session.with_transaction(transaction)


@celery_app.task(
    bind=True, name="app.ingestion.tasks.process_ingestion_job", max_retries=2
)
def process_ingestion_job(self, job_id: str):
    client = MongoClient(
        settings.mongodb_url, serverSelectionTimeoutMS=5000, tz_aware=True
    )
    db = client[settings.mongodb_database]
    job = None
    try:
        now = datetime.now(UTC)
        oid = ObjectId(job_id)
        job = db.ingestion_jobs.find_one_and_update(
            {
                "_id": oid,
                "status": {"$in": ["QUEUED", "PROCESSING"]},
                "$expr": {"$lt": ["$attempt_count", "$max_attempts"]},
                "$and": [
                    {
                        "$or": [
                            {"next_retry_at": {"$lte": now}},
                            {"next_retry_at": {"$exists": False}},
                        ]
                    }
                ],
                "$or": [
                    {"status": "QUEUED"},
                    {"lease_expires_at": {"$lte": now}},
                    {"lease_expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "status": "PROCESSING",
                    "stage": IngestionStage.EXTRACTING.value,
                    "lease_owner": self.request.id,
                    "lease_expires_at": now + _LEASE,
                    "dispatch_status": "DISPATCHED",
                    "updated_at": now,
                },
                "$inc": {"attempt_count": 1, "fencing_token": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if job is None:
            raise Ignore()
        db.documents.update_one(
            {
                "_id": job["document_id"],
                "deleted_at": {"$exists": False},
                "active_index_version": None,
            },
            {"$set": {"status": "PROCESSING", "updated_at": now}},
        )
        document = db.documents.find_one(
            {"_id": job["document_id"], "deleted_at": {"$exists": False}}
        )
        if document is None:
            db.ingestion_jobs.update_one(
                {"_id": job["_id"], "fencing_token": job["fencing_token"]},
                {
                    "$set": {
                        "status": "FAILED",
                        "error_code": "DOCUMENT_DELETED",
                        "error_message": "The document is no longer available.",
                        "updated_at": now,
                    }
                },
            )
            raise Ignore()

        connection = settings.azure_storage_connection_string or _AZURITE_CONNECTION
        service = BlobServiceClient.from_connection_string(
            connection, api_version=settings.azure_storage_api_version
        )
        try:
            content = (
                service.get_blob_client(
                    settings.azure_blob_container, document["blob_key"]
                )
                .download_blob()
                .readall()
            )
        finally:
            service.close()
        pages = extract_pages(document["filename"], content)
        _stage(db, job, IngestionStage.CHUNKING)
        provider = embedding_provider()
        chunks = chunk_pages(
            pages,
            settings.document_chunk_size,
            settings.document_chunk_overlap,
            tokenizer=provider,
            max_input_tokens=provider.max_input_tokens,
        )
        if not chunks:
            raise RuntimeError("DOCUMENT_TEXT_EMPTY")
        db.documents.update_one(
            {"_id": document["_id"], "deleted_at": {"$exists": False}},
            {"$set": {"chunk_count": len(chunks), "updated_at": datetime.now(UTC)}},
        )
        _stage(db, job, IngestionStage.EMBEDDING)
        embeddings = _embed_chunks(db, job, chunks, provider)
        _stage(db, job, IngestionStage.INDEXING)
        index = pinecone_index()
        _begin_index_write(db, client, job, document)
        try:
            namespace = _upsert_and_verify(
                index,
                document,
                job["target_index_version"],
                chunks,
                embeddings,
                provider.dimension,
                before_batch=lambda: _renew_index_write(db, job),
            )
        finally:
            _finish_index_write(db, job, document)
        _stage(db, job, IngestionStage.VERIFYING)
        previous_version = _activate(db, client, job, document, namespace, len(chunks))
        if (
            previous_version is not None
            and previous_version != job["target_index_version"]
        ):
            try:
                cleanup_result = cleanup_old_index_version.delay(str(job["_id"]))
                db.ingestion_jobs.update_one(
                    {"_id": job["_id"], "index_cleanup_status": "PENDING"},
                    {
                        "$set": {
                            "index_cleanup_status": "DISPATCHED",
                            "index_cleanup_task_id": cleanup_result.id,
                            "index_cleanup_dispatched_at": datetime.now(UTC),
                        }
                    },
                )
            except Exception:
                logger.exception(
                    "Old index cleanup dispatch failed; reconciliation will retry"
                )
    except Ignore:
        raise
    except Exception as error:  # noqa: BLE001 - task boundary maps failures to durable job state
        detail = getattr(error, "detail", None)
        detail_code = detail.get("code") if isinstance(detail, dict) else None
        known_codes = {
            "INDEX_PROVIDER_UNCONFIGURED",
            "EMBEDDING_DIMENSION_MISMATCH",
            "LOCAL_EMBEDDING_DIMENSION_MISMATCH",
            "CHUNK_SIZE_EXCEEDS_EMBEDDING_LIMIT",
            "INDEX_VERSION_COUNT_MISMATCH",
            "INDEX_VERSION_NOT_VERIFIED",
            "DOCUMENT_TEXT_EMPTY",
            "INGESTION_LEASE_LOST",
            "DOCUMENT_STATE_CHANGED",
            "OCR_REQUIRED",
            "PDF_ENCRYPTED",
            "PDF_PAGE_LIMIT",
            "DOCUMENT_PARSE_FAILED",
            "EXTRACTED_TEXT_LIMIT",
            "FILE_TYPE_UNSUPPORTED",
        }
        code = (
            str(error)
            if str(error) in known_codes
            else detail_code or "INGESTION_FAILED"
        )
        if code in {"INGESTION_LEASE_LOST", "DOCUMENT_STATE_CHANGED"}:
            raise Ignore() from None
        deterministic = code in {
            "INDEX_PROVIDER_UNCONFIGURED",
            "EMBEDDING_DIMENSION_MISMATCH",
            "LOCAL_EMBEDDING_DIMENSION_MISMATCH",
            "CHUNK_SIZE_EXCEEDS_EMBEDDING_LIMIT",
            "DOCUMENT_TEXT_EMPTY",
            "OCR_REQUIRED",
            "PDF_ENCRYPTED",
            "PDF_PAGE_LIMIT",
            "DOCUMENT_PARSE_FAILED",
            "FILE_TYPE_UNSUPPORTED",
        }
        if job and not deterministic and job["attempt_count"] < job["max_attempts"]:
            delay = 2 ** job["attempt_count"]
            db.ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "fencing_token": job["fencing_token"],
                    "status": "PROCESSING",
                },
                {
                    "$set": {
                        "status": "QUEUED",
                        "dispatch_status": "PENDING",
                        "next_retry_at": datetime.now(UTC) + timedelta(seconds=delay),
                        "updated_at": datetime.now(UTC),
                    },
                    "$unset": {"lease_owner": "", "lease_expires_at": ""},
                },
            )
            raise self.retry(exc=error, countdown=delay)
        safe_message = {
            "INDEX_PROVIDER_UNCONFIGURED": "Configure Azure OpenAI embeddings and the Pinecone index to finish indexing.",
            "EMBEDDING_DIMENSION_MISMATCH": "The embedding dimensions do not match the configured Pinecone index.",
            "LOCAL_EMBEDDING_DIMENSION_MISMATCH": "The local embedding model did not produce the configured 384 dimensions.",
            "CHUNK_SIZE_EXCEEDS_EMBEDDING_LIMIT": "The configured chunk size exceeds the selected embedding model input limit.",
            "OCR_REQUIRED": "This PDF has no extractable text; scanned-document OCR is not available.",
        }.get(code, "The document could not be processed safely.")
        now = datetime.now(UTC)
        if job:
            failed = db.ingestion_jobs.update_one(
                {
                    "_id": job["_id"],
                    "fencing_token": job["fencing_token"],
                    "status": "PROCESSING",
                },
                {
                    "$set": {
                        "status": "FAILED",
                        "error_code": code,
                        "error_message": safe_message,
                        "updated_at": now,
                    },
                    "$unset": {
                        "lease_owner": "",
                        "lease_expires_at": "",
                        "next_retry_at": "",
                    },
                },
            )
            if failed.modified_count == 1:
                doc_status = "OCR_REQUIRED" if code == "OCR_REQUIRED" else "FAILED"
                db.documents.update_one(
                    {
                        "_id": job["document_id"],
                        "deleted_at": {"$exists": False},
                        "active_index_version": None,
                    },
                    {"$set": {"status": doc_status, "updated_at": now}},
                )
                db.audit_logs.insert_one(
                    AuditRecord(
                        actor_id=document["uploaded_by"]
                        if "document" in locals() and document
                        else None,
                        event="ingestion.failed",
                        resource_type="document",
                        resource_id=job["document_id"],
                        request_id=str(job["_id"]),
                        outcome="FAILURE",
                        metadata={
                            "error_code": code,
                            "attempt_count": job["attempt_count"],
                        },
                    ).bson()
                )
    finally:
        client.close()


@celery_app.task(name="app.ingestion.tasks.reconcile_pending_ingestion_jobs")
def reconcile_pending_ingestion_jobs():
    client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    db = client[settings.mongodb_database]
    try:
        now = datetime.now(UTC)
        # Published messages can disappear with a broker restore/recreation
        # before any worker claims a Mongo lease. Republish aged durable intents.
        cutoff = now - _DISPATCH_TIMEOUT
        db.ingestion_jobs.update_many(
            {
                "status": "QUEUED",
                "dispatch_status": "DISPATCHED",
                "$or": [
                    {"dispatched_at": {"$lte": cutoff}},
                    {
                        "dispatched_at": {"$exists": False},
                        "updated_at": {"$lte": cutoff},
                    },
                ],
            },
            {
                "$set": {"dispatch_status": "PENDING", "updated_at": now},
                "$unset": {"dispatch_task_id": ""},
            },
        )
        stale_jobs = db.ingestion_jobs.find(
            {"status": "PROCESSING", "lease_expires_at": {"$lte": now}},
            {
                "_id": 1,
                "document_id": 1,
                "attempt_count": 1,
                "max_attempts": 1,
                "fencing_token": 1,
            },
        ).limit(100)
        for stale in stale_jobs:
            exhausted = stale["attempt_count"] >= stale["max_attempts"]
            update = {
                "$set": {
                    "status": "FAILED" if exhausted else "QUEUED",
                    "dispatch_status": "FAILED" if exhausted else "PENDING",
                    "error_code": "WORKER_LEASE_EXPIRED" if exhausted else None,
                    "error_message": "Processing stopped unexpectedly; an administrator can retry this job."
                    if exhausted
                    else None,
                    "updated_at": now,
                },
                "$unset": {"lease_owner": "", "lease_expires_at": ""},
                "$inc": {"fencing_token": 1},
            }
            changed = db.ingestion_jobs.update_one(
                {
                    "_id": stale["_id"],
                    "status": "PROCESSING",
                    "fencing_token": stale["fencing_token"],
                    "lease_expires_at": {"$lte": now},
                },
                update,
            )
            if exhausted and changed.modified_count == 1:
                db.documents.update_one(
                    {
                        "_id": stale["document_id"],
                        "deleted_at": {"$exists": False},
                        "active_index_version": None,
                    },
                    {"$set": {"status": "FAILED", "updated_at": now}},
                )
                db.audit_logs.insert_one(
                    AuditRecord(
                        actor_id=None,
                        event="ingestion.worker_lease_expired",
                        resource_type="document",
                        resource_id=stale["document_id"],
                        request_id=str(stale["_id"]),
                        outcome="FAILURE",
                        metadata={"attempt_count": stale["attempt_count"]},
                    ).bson()
                )
        jobs = db.ingestion_jobs.find(
            {
                "status": "QUEUED",
                "dispatch_status": "PENDING",
                "$or": [
                    {"next_retry_at": {"$lte": now}},
                    {"next_retry_at": {"$exists": False}},
                ],
            },
            {"_id": 1},
        ).limit(100)
        for job in jobs:
            result = process_ingestion_job.delay(str(job["_id"]))
            db.ingestion_jobs.update_one(
                {"_id": job["_id"], "status": "QUEUED", "dispatch_status": "PENDING"},
                {
                    "$set": {
                        "dispatch_status": "DISPATCHED",
                        "dispatched_at": now,
                        "updated_at": now,
                        "dispatch_task_id": result.id,
                    }
                },
            )
        old_indexes = db.ingestion_jobs.find(
            {
                "status": "SUCCEEDED",
                "previous_index_version": {"$ne": None},
                "index_cleanup_attempt_count": {"$lt": 3},
                "$or": [
                    {"index_cleanup_status": {"$in": ["PENDING", "FAILED"]}},
                    {
                        "index_cleanup_status": "DISPATCHED",
                        "index_cleanup_dispatched_at": {
                            "$lte": now - timedelta(minutes=5)
                        },
                    },
                    {
                        "index_cleanup_status": "PROCESSING",
                        "index_cleanup_lease_expires_at": {"$lte": now},
                    },
                ],
            },
            {"_id": 1, "index_cleanup_status": 1},
        ).limit(100)
        for old_index in old_indexes:
            reset = db.ingestion_jobs.update_one(
                {
                    "_id": old_index["_id"],
                    "status": "SUCCEEDED",
                    "index_cleanup_status": old_index["index_cleanup_status"],
                },
                {
                    "$set": {"index_cleanup_status": "PENDING", "updated_at": now},
                    "$inc": {"index_cleanup_fencing_token": 1},
                },
            )
            if reset.modified_count == 1:
                result = cleanup_old_index_version.delay(str(old_index["_id"]))
                db.ingestion_jobs.update_one(
                    {"_id": old_index["_id"], "index_cleanup_status": "PENDING"},
                    {
                        "$set": {
                            "index_cleanup_status": "DISPATCHED",
                            "index_cleanup_task_id": result.id,
                            "index_cleanup_dispatched_at": now,
                        }
                    },
                )
        cleanup = db.documents.find(
            {
                "deleted_at": {"$exists": True},
                "cleanup_attempt_count": {"$lt": 3},
                "$or": [
                    {"cleanup_dispatch_status": {"$in": ["PENDING", "FAILED"]}},
                    {
                        "cleanup_dispatch_status": "DISPATCHED",
                        "cleanup_dispatched_at": {"$lte": now - timedelta(minutes=5)},
                    },
                    {
                        "cleanup_dispatch_status": "PROCESSING",
                        "cleanup_lease_expires_at": {"$lte": now},
                    },
                ],
            },
            {"_id": 1, "cleanup_dispatch_status": 1, "cleanup_fencing_token": 1},
        ).limit(100)
        for document in cleanup:
            reset = db.documents.update_one(
                {
                    "_id": document["_id"],
                    "deleted_at": {"$exists": True},
                    "cleanup_dispatch_status": document.get("cleanup_dispatch_status"),
                },
                {
                    "$set": {"cleanup_dispatch_status": "PENDING", "updated_at": now},
                    "$inc": {"cleanup_fencing_token": 1},
                },
            )
            if reset.modified_count != 1:
                continue
            result = cleanup_deleted_document.delay(str(document["_id"]))
            db.documents.update_one(
                {"_id": document["_id"], "cleanup_dispatch_status": "PENDING"},
                {
                    "$set": {
                        "cleanup_dispatch_status": "DISPATCHED",
                        "cleanup_task_id": result.id,
                        "cleanup_dispatched_at": now,
                    }
                },
            )
    finally:
        client.close()


@celery_app.task(
    bind=True, name="app.ingestion.tasks.cleanup_old_index_version", max_retries=2
)
def cleanup_old_index_version(self, job_id: str):
    client = MongoClient(
        settings.mongodb_url, serverSelectionTimeoutMS=5000, tz_aware=True
    )
    db = client[settings.mongodb_database]
    try:
        oid = ObjectId(job_id)
        now = datetime.now(UTC)
        job = db.ingestion_jobs.find_one(
            {"_id": oid, "status": "SUCCEEDED", "previous_index_version": {"$ne": None}}
        )
        if job is None:
            raise Ignore()
        claim = db.ingestion_jobs.update_one(
            {
                "_id": oid,
                "status": "SUCCEEDED",
                "index_cleanup_attempt_count": {"$lt": 3},
                "$or": [
                    {
                        "index_cleanup_status": {
                            "$in": ["PENDING", "DISPATCHED", "FAILED"]
                        }
                    },
                    {
                        "index_cleanup_status": "PROCESSING",
                        "index_cleanup_lease_expires_at": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "index_cleanup_status": "PROCESSING",
                    "index_cleanup_lease_expires_at": now + _LEASE,
                },
                "$inc": {
                    "index_cleanup_attempt_count": 1,
                    "index_cleanup_fencing_token": 1,
                },
            },
        )
        if claim.modified_count != 1:
            raise Ignore()
        job = db.ingestion_jobs.find_one({"_id": oid})
        fence = job["index_cleanup_fencing_token"]
        if not settings.pinecone_api_key or not settings.pinecone_index_name:
            raise RuntimeError("VECTOR_CLEANUP_UNCONFIGURED")
        try:
            pinecone_index().delete(
                delete_all=True,
                namespace=f"{job['document_id']}-v{job['previous_index_version']}",
            )
        except Exception as error:
            if not _is_not_found(error):
                raise
        db.ingestion_jobs.update_one(
            {
                "_id": oid,
                "index_cleanup_fencing_token": fence,
                "index_cleanup_status": "PROCESSING",
            },
            {
                "$set": {
                    "index_cleanup_status": "SUCCEEDED",
                    "index_cleanup_completed_at": datetime.now(UTC),
                },
                "$unset": {
                    "index_cleanup_lease_expires_at": "",
                    "index_cleanup_error_code": "",
                },
            },
        )
    except Ignore:
        raise
    except Exception as error:  # noqa: BLE001 - task boundary persists cleanup state
        if "job" in locals() and job is not None:
            attempt = int(job.get("index_cleanup_attempt_count", 0))
            final = attempt >= 3
            db.ingestion_jobs.update_one(
                {
                    "_id": oid,
                    "index_cleanup_fencing_token": job.get(
                        "index_cleanup_fencing_token"
                    ),
                    "index_cleanup_status": "PROCESSING",
                },
                {
                    "$set": {
                        "index_cleanup_status": "FAILED" if final else "PENDING",
                        "index_cleanup_error_code": "VECTOR_CLEANUP_UNCONFIGURED"
                        if str(error) == "VECTOR_CLEANUP_UNCONFIGURED"
                        else "CLEANUP_FAILED",
                    },
                    "$unset": {"index_cleanup_lease_expires_at": ""},
                },
            )
            if not final:
                raise self.retry(exc=error, countdown=2 ** max(1, attempt))
    finally:
        client.close()


@celery_app.task(
    bind=True, name="app.ingestion.tasks.cleanup_deleted_document", max_retries=2
)
def cleanup_deleted_document(self, document_id: str):
    client = MongoClient(
        settings.mongodb_url, serverSelectionTimeoutMS=5000, tz_aware=True
    )
    db = client[settings.mongodb_database]
    try:
        oid = ObjectId(document_id)
        now = datetime.now(UTC)
        doc = db.documents.find_one({"_id": oid, "deleted_at": {"$exists": True}})
        if doc is None:
            raise Ignore()
        if db.ingestion_jobs.find_one(
            {
                "document_id": oid,
                "vector_write_pending": True,
                "vector_write_expires_at": {"$gt": now},
            }
        ):
            # Do not spend a cleanup attempt or report success while a live
            # remote writer can recreate the namespace. Its finally block, or
            # the five-minute reconciler after lease expiry, will retry cleanup.
            raise Ignore()
        claim = db.documents.update_one(
            {
                "_id": oid,
                "deleted_at": {"$exists": True},
                "cleanup_attempt_count": {"$lt": 3},
                "$or": [
                    {
                        "cleanup_dispatch_status": {
                            "$in": ["PENDING", "DISPATCHED", "FAILED"]
                        }
                    },
                    {
                        "cleanup_dispatch_status": "PROCESSING",
                        "cleanup_lease_expires_at": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "cleanup_dispatch_status": "PROCESSING",
                    "cleanup_lease_expires_at": now + _LEASE,
                    "updated_at": now,
                },
                "$inc": {"cleanup_attempt_count": 1, "cleanup_fencing_token": 1},
            },
        )
        if claim.modified_count != 1:
            raise Ignore()
        doc = db.documents.find_one({"_id": oid})
        attempt = int(doc.get("cleanup_attempt_count", 1))
        fence = int(doc.get("cleanup_fencing_token", 1))
        jobs = list(
            db.ingestion_jobs.find(
                {"document_id": oid}, {"target_index_version": 1, "stage": 1}
            )
        )
        needs_vector_cleanup = doc.get("active_index_version") is not None or any(
            row.get("stage") in {"INDEXING", "VERIFYING", "COMPLETE"} for row in jobs
        )
        if needs_vector_cleanup:
            if not settings.pinecone_api_key or not settings.pinecone_index_name:
                raise RuntimeError("VECTOR_CLEANUP_UNCONFIGURED")
            index = pinecone_index()
            versions = {row["target_index_version"] for row in jobs}
            if doc.get("active_index_version") is not None:
                versions.add(doc["active_index_version"])
            for version in versions:
                try:
                    index.delete(delete_all=True, namespace=f"{oid}-v{version}")
                except Exception as error:
                    if not _is_not_found(error):
                        raise
        connection = settings.azure_storage_connection_string or _AZURITE_CONNECTION
        service = BlobServiceClient.from_connection_string(
            connection, api_version=settings.azure_storage_api_version
        )
        try:
            service.get_blob_client(
                settings.azure_blob_container, doc["blob_key"]
            ).delete_blob(delete_snapshots="include")
        except Exception as error:
            from azure.core.exceptions import ResourceNotFoundError

            if not isinstance(error, ResourceNotFoundError):
                raise
        finally:
            service.close()
        db.documents.update_one(
            {
                "_id": oid,
                "deleted_at": {"$exists": True},
                "cleanup_fencing_token": fence,
                "cleanup_dispatch_status": "PROCESSING",
            },
            {
                "$set": {
                    "cleanup_dispatch_status": "SUCCEEDED",
                    "cleanup_completed_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
                "$unset": {"cleanup_error_code": "", "cleanup_lease_expires_at": ""},
            },
        )
    except Ignore:
        raise
    except Exception as error:  # noqa: BLE001 - task boundary persists cleanup state
        code = (
            str(error)
            if str(error) == "VECTOR_CLEANUP_UNCONFIGURED"
            else "CLEANUP_FAILED"
        )
        if "doc" in locals() and doc is not None:
            final = int(doc.get("cleanup_attempt_count", 0)) >= 3
            db.documents.update_one(
                {
                    "_id": oid,
                    "deleted_at": {"$exists": True},
                    "cleanup_fencing_token": doc.get("cleanup_fencing_token"),
                    "cleanup_dispatch_status": "PROCESSING",
                },
                {
                    "$set": {
                        "cleanup_dispatch_status": "FAILED" if final else "PENDING",
                        "cleanup_error_code": code,
                        "updated_at": datetime.now(UTC),
                    },
                    "$unset": {"cleanup_lease_expires_at": ""},
                },
            )
            if not final:
                raise self.retry(
                    exc=error,
                    countdown=2 ** max(1, int(doc.get("cleanup_attempt_count", 1))),
                )
    finally:
        client.close()
