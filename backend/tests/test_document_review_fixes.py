"""Regressions for upload limits, ACL races, dispatch recovery and late writes."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bson import ObjectId
from celery.exceptions import Ignore
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient
from pymongo import MongoClient
from test_admin_users import env  # noqa: F401 - shared isolated real-service fixture

from app.auth.errors import install_auth_errors
from app.auth.tokens import decode_access_token
from app.documents import service
from app.documents.body_limit import UploadBodyLimitMiddleware
from app.documents.schemas import DocumentPermissionsRequest
from app.documents.storage import ensure_document_storage
from app.ingestion import tasks


@pytest.mark.asyncio
async def test_acl_patch_uses_current_transaction_state(env):  # noqa: F811
    db = env["db"]
    await ensure_document_storage(db)
    actor = await db.users.find_one({"_id": ObjectId(env["admin_id"])})
    claims = decode_access_token(env["admin"]["access_token"])
    actor["_auth_session"] = await db.auth_sessions.find_one(
        {"_id": ObjectId(claims["sid"])}
    )
    colleague = ObjectId()
    await db.users.insert_one(
        {
            "_id": colleague,
            "status": "ACTIVE",
            "department_id": actor["department_id"],
            "email_normalized": "colleague@example.com",
        }
    )
    now = datetime.now(UTC)
    doc = {
        "_id": ObjectId(),
        "filename": "acl.txt",
        "mime_type": "text/plain",
        "size_bytes": 1,
        "sha256": uuid4().hex,
        "duplicate_scope_key": f"PRIVATE:{actor['_id']}",
        "uploaded_by": actor["_id"],
        "department_id": actor["department_id"],
        "visibility": "PRIVATE",
        "allowed_user_ids": [colleague],
        "status": "READY",
        "created_at": now,
        "updated_at": now,
    }
    await db.documents.insert_one(doc)
    # Two handlers read before either transaction commits. An update to another
    # field must not restore users from that request's stale snapshot.
    stale = dict(doc)
    await service.update_document_permissions(
        db, actor, doc, DocumentPermissionsRequest(allowed_user_ids=[]), "revoke"
    )
    await service.update_document_permissions(
        db,
        actor,
        stale,
        DocumentPermissionsRequest(visibility="PRIVATE"),
        "visibility-only",
    )
    current = await db.documents.find_one({"_id": doc["_id"]})
    assert current["allowed_user_ids"] == []

    await db.documents.update_one(
        {"_id": doc["_id"]}, {"$set": {"visibility": "DEPARTMENT"}}
    )
    stale = await db.documents.find_one({"_id": doc["_id"]})
    await service.update_document_permissions(
        db,
        actor,
        stale,
        DocumentPermissionsRequest(visibility="PRIVATE"),
        "make-private",
    )
    await service.update_document_permissions(
        db,
        actor,
        stale,
        DocumentPermissionsRequest(allowed_user_ids=[str(colleague)]),
        "share-only",
    )
    current = await db.documents.find_one({"_id": doc["_id"]})
    assert current["visibility"] == "PRIVATE"
    assert current["allowed_user_ids"] == [colleague]


def upload_app(limit=2048):
    app = FastAPI()
    install_auth_errors(app)
    app.add_middleware(UploadBodyLimitMiddleware, max_body_bytes=limit)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"])

    @app.post("/api/v1/documents")
    async def upload(file: UploadFile = File()):
        content = await file.read()
        await file.close()
        return {"size": len(content)}

    return app


@pytest.mark.asyncio
async def test_oversized_content_length_is_rejected_without_reading_body():
    consumed = False

    async def body():
        nonlocal consumed
        consumed = True
        yield b"not supposed to be read"

    async with AsyncClient(
        transport=ASGITransport(app=upload_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/documents",
            content=body(),
            headers={"Content-Length": "999999", "Origin": "http://localhost:3000"},
        )
    assert response.status_code == 413
    assert not consumed
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [None, "1"])
async def test_chunked_or_underreported_upload_stops_at_stream_budget(length):
    consumed = 0

    async def body():
        nonlocal consumed
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\nContent-Type: text/plain\r\n\r\n'
        for _ in range(100):
            consumed += 512
            yield b"x" * 512
        yield b"\r\n--test--\r\n"

    headers = {
        "Content-Type": "multipart/form-data; boundary=test",
        "Origin": "http://localhost:3000",
    }
    if length:
        headers["Content-Length"] = length
    async with AsyncClient(
        transport=ASGITransport(app=upload_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/documents", content=body(), headers=headers
        )
    assert response.status_code == 413
    assert consumed <= 2048 + 512
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


@pytest.mark.asyncio
async def test_valid_multipart_upload_with_overhead_is_accepted():
    async with AsyncClient(
        transport=ASGITransport(app=upload_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("small.txt", b"x" * 1024, "text/plain")},
        )
    assert response.status_code == 200
    assert response.json() == {"size": 1024}


@pytest.fixture
def worker_db(monkeypatch):
    url = os.environ.get("AUTH_TEST_MONGODB_URL")
    if not url:
        pytest.skip("Set AUTH_TEST_MONGODB_URL to run worker recovery regressions")
    client = MongoClient(
        url, tz_aware=True, directConnection=True, serverSelectionTimeoutMS=5000
    )
    name = "test_ingestion_review_" + uuid4().hex
    monkeypatch.setattr(tasks.settings, "mongodb_url", url)
    monkeypatch.setattr(tasks.settings, "mongodb_database", name)
    try:
        assert client.admin.command("hello").get("setName")
        yield client, client[name]
    finally:
        assert name.startswith("test_ingestion_review_")
        client.drop_database(name)
        client.close()


def test_reconciler_redelivers_old_dispatch_but_not_recent_or_running(
    worker_db, monkeypatch
):
    _, db = worker_db
    now = datetime.now(UTC)
    old = now - timedelta(hours=2)
    ids = [ObjectId() for _ in range(4)]
    for oid, fields in zip(
        ids,
        [
            {"status": "QUEUED", "dispatch_status": "DISPATCHED", "dispatched_at": old},
            {"status": "QUEUED", "dispatch_status": "DISPATCHED"},  # legacy row
            {"status": "QUEUED", "dispatch_status": "DISPATCHED", "dispatched_at": now},
            {
                "status": "PROCESSING",
                "dispatch_status": "DISPATCHED",
                "lease_expires_at": now + timedelta(hours=1),
            },
        ],
        strict=True,
    ):
        db.ingestion_jobs.insert_one(
            {
                "_id": oid,
                "document_id": ObjectId(),
                "attempt_count": 0,
                "max_attempts": 3,
                "fencing_token": 0,
                "updated_at": old,
                **fields,
            }
        )
    delivered = []

    def dispatch(oid):
        delivered.append(oid)
        return SimpleNamespace(id=uuid4().hex)

    monkeypatch.setattr(tasks.process_ingestion_job, "delay", dispatch)
    tasks.reconcile_pending_ingestion_jobs.run()
    assert set(delivered) == {str(ids[0]), str(ids[1])}
    assert db.ingestion_jobs.find_one({"_id": ids[3]})["status"] == "PROCESSING"
    delivered.clear()
    tasks.reconcile_pending_ingestion_jobs.run()
    assert delivered == []


@pytest.mark.parametrize(
    "fields",
    [
        {"attempt_count": 3},
        {"next_retry_at": datetime.now(UTC) + timedelta(hours=1)},
    ],
)
def test_redelivery_respects_attempt_budget_and_backoff(worker_db, fields):
    _, db = worker_db
    oid = ObjectId()
    db.ingestion_jobs.insert_one(
        {
            "_id": oid,
            "document_id": ObjectId(),
            "status": "QUEUED",
            "attempt_count": 0,
            "max_attempts": 3,
            "fencing_token": 0,
            **fields,
        }
    )
    with pytest.raises(Ignore):
        tasks.process_ingestion_job.run(str(oid))
    assert db.ingestion_jobs.find_one({"_id": oid})["fencing_token"] == 0


class FakeBlobService:
    @classmethod
    def from_connection_string(cls, *args, **kwargs):
        return cls()

    def get_blob_client(self, *args, **kwargs):
        return self

    def download_blob(self):
        return self

    def readall(self):
        return b"Synthetic document text for a late-write regression."

    def delete_blob(self, **kwargs):
        pass

    def close(self):
        pass


class FakeProvider:
    dimension = 3
    max_input_tokens = 254
    batch_size = 8

    def tokenize(self, text):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)

    def embed(self, texts):
        return [[0.0, 0.0, 1.0] for _ in texts]


def test_deletion_waits_for_remote_writer_then_removes_late_vectors(
    worker_db, monkeypatch
):
    _, db = worker_db
    doc_id, job_id = ObjectId(), ObjectId()
    now = datetime.now(UTC)
    db.documents.insert_one(
        {
            "_id": doc_id,
            "filename": "late.txt",
            "uploaded_by": ObjectId(),
            "blob_key": "synthetic",
            "status": "QUEUED",
            "active_index_version": None,
        }
    )
    db.ingestion_jobs.insert_one(
        {
            "_id": job_id,
            "document_id": doc_id,
            "target_index_version": 1,
            "status": "QUEUED",
            "stage": "VALIDATING",
            "attempt_count": 0,
            "max_attempts": 3,
            "fencing_token": 0,
        }
    )

    class Index:
        def __init__(self):
            self.vectors = {}
            self.namespace = None

        def describe_index_stats(self, **kwargs):
            return SimpleNamespace(
                dimension=3,
                namespaces={}
                if self.namespace is None
                else {self.namespace: SimpleNamespace(vector_count=len(self.vectors))},
            )

        def delete(self, **kwargs):
            self.vectors.clear()

        def upsert(self, vectors, namespace, **kwargs):
            self.namespace = namespace
            db.documents.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "deleted_at": now,
                        "status": "DELETED",
                        "cleanup_dispatch_status": "PENDING",
                        "cleanup_attempt_count": 0,
                    }
                },
            )
            db.ingestion_jobs.update_one(
                {"_id": job_id},
                {"$set": {"status": "FAILED"}, "$inc": {"fencing_token": 1}},
            )
            # Another worker sees the registered write intent and defers cleanup.
            with pytest.raises(Ignore):
                tasks.cleanup_deleted_document.run(str(doc_id))
            assert db.documents.find_one({"_id": doc_id})["cleanup_attempt_count"] == 0
            self.vectors.update({row[0]: row for row in vectors})

    index = Index()
    monkeypatch.setattr(tasks, "BlobServiceClient", FakeBlobService)
    monkeypatch.setattr(tasks, "embedding_provider", FakeProvider)
    monkeypatch.setattr(tasks, "pinecone_index", lambda: index)
    monkeypatch.setattr(tasks.settings, "pinecone_api_key", "synthetic-test-double")
    monkeypatch.setattr(tasks.settings, "pinecone_index_name", "synthetic-test-double")
    with pytest.raises(Ignore):
        tasks.process_ingestion_job.run(str(job_id))
    assert index.vectors
    assert not db.ingestion_jobs.find_one({"_id": job_id})["vector_write_pending"]
    assert (
        db.documents.find_one({"_id": doc_id})["cleanup_dispatch_status"] == "PENDING"
    )
    tasks.cleanup_deleted_document.run(str(doc_id))
    assert not index.vectors
    assert (
        db.documents.find_one({"_id": doc_id})["cleanup_dispatch_status"] == "SUCCEEDED"
    )


def test_late_writer_reopens_previously_successful_cleanup(worker_db):
    _, db = worker_db
    doc_id, job_id = ObjectId(), ObjectId()
    db.documents.insert_one(
        {
            "_id": doc_id,
            "deleted_at": datetime.now(UTC),
            "cleanup_dispatch_status": "SUCCEEDED",
            "cleanup_attempt_count": 1,
        }
    )
    db.ingestion_jobs.insert_one(
        {
            "_id": job_id,
            "fencing_token": 2,
            "vector_write_token": 1,
            "vector_write_pending": True,
        }
    )
    tasks._finish_index_write(db, {"_id": job_id, "fencing_token": 1}, {"_id": doc_id})
    assert (
        db.documents.find_one({"_id": doc_id})["cleanup_dispatch_status"] == "PENDING"
    )
    assert not db.ingestion_jobs.find_one({"_id": job_id})["vector_write_pending"]


def test_deleted_document_cannot_start_external_write(worker_db):
    client, db = worker_db
    doc_id, job_id = ObjectId(), ObjectId()
    db.documents.insert_one({"_id": doc_id, "deleted_at": datetime.now(UTC)})
    db.ingestion_jobs.insert_one(
        {"_id": job_id, "status": "PROCESSING", "fencing_token": 1}
    )
    with pytest.raises(RuntimeError, match="INGESTION_LEASE_LOST"):
        tasks._begin_index_write(
            db, client, {"_id": job_id, "fencing_token": 1}, {"_id": doc_id}
        )
    assert not db.ingestion_jobs.find_one({"_id": job_id}).get("vector_write_pending")


def test_crashed_writer_expiry_allows_cleanup(worker_db, monkeypatch):
    _, db = worker_db
    doc_id = ObjectId()
    db.documents.insert_one(
        {
            "_id": doc_id,
            "deleted_at": datetime.now(UTC),
            "blob_key": "synthetic",
            "active_index_version": None,
            "cleanup_attempt_count": 0,
            "cleanup_dispatch_status": "PENDING",
        }
    )
    db.ingestion_jobs.insert_one(
        {
            "document_id": doc_id,
            "status": "FAILED",
            "stage": "INDEXING",
            "target_index_version": 1,
            "vector_write_pending": True,
            "vector_write_expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    deleted = []
    monkeypatch.setattr(tasks, "BlobServiceClient", FakeBlobService)
    monkeypatch.setattr(
        tasks,
        "pinecone_index",
        lambda: SimpleNamespace(
            delete=lambda **kwargs: deleted.append(kwargs["namespace"])
        ),
    )
    monkeypatch.setattr(tasks.settings, "pinecone_api_key", "synthetic-test-double")
    monkeypatch.setattr(tasks.settings, "pinecone_index_name", "synthetic-test-double")
    tasks.cleanup_deleted_document.run(str(doc_id))
    assert deleted == [f"{doc_id}-v1"]
    assert (
        db.documents.find_one({"_id": doc_id})["cleanup_dispatch_status"] == "SUCCEEDED"
    )
