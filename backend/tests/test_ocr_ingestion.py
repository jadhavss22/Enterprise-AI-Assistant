# ruff: noqa: F811
import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bson import ObjectId
from celery.exceptions import Retry
from test_admin_users import bearer, env  # noqa: F401
from test_chat_integration import events
from test_document_review_fixes import FakeBlobService, FakeProvider, worker_db  # noqa: F401
from test_ocr import require_engine, scanned_pdf

from app.chat import service as chat_service
from app.chat.storage import ensure_chat_storage
from app.config import settings
from app.documents import service as documents
from app.documents.storage import ensure_document_storage
from app.ingestion import ocr, tasks
from app.retrieval import service as retrieval


@pytest.mark.asyncio
async def test_uploaded_scan_is_indexed_searchable_and_cited_on_original_page(
    env, monkeypatch
):
    require_engine()
    db, client = env["db"], env["client"]
    await ensure_document_storage(db)
    await ensure_chat_storage(db)
    blobs, indexed, progress = {}, {}, []

    async def upload_blob(key, content, mime_type):
        blobs[key] = content

    class Blobs(FakeBlobService):
        def get_blob_client(self, container, key):
            return SimpleNamespace(
                download_blob=lambda: SimpleNamespace(readall=lambda: blobs[key])
            )

    class Index:
        def describe_index_stats(self, **kwargs):
            return SimpleNamespace(
                dimension=3,
                namespaces={
                    namespace: SimpleNamespace(vector_count=len(rows))
                    for namespace, rows in indexed.items()
                },
            )

        def upsert(self, vectors, namespace, **kwargs):
            indexed.setdefault(namespace, {}).update({row[0]: row for row in vectors})

    original_progress = tasks._ocr_progress

    def observe(db, job, page, completed, total):
        original_progress(db, job, page, completed, total)
        progress.append((page, completed, total))

    monkeypatch.setattr(documents, "upload_private_blob", upload_blob)
    monkeypatch.setattr(
        tasks.process_ingestion_job,
        "delay",
        lambda *args: SimpleNamespace(id="synthetic"),
    )
    monkeypatch.setattr(tasks, "BlobServiceClient", Blobs)
    monkeypatch.setattr(tasks, "embedding_provider", FakeProvider)
    monkeypatch.setattr(tasks, "pinecone_index", Index)
    monkeypatch.setattr(tasks, "_ocr_progress", observe)
    monkeypatch.setattr(settings, "mongodb_url", os.environ["AUTH_TEST_MONGODB_URL"])
    monkeypatch.setattr(settings, "mongodb_database", db.name)
    headers = bearer(env["admin"])
    response = await client.post(
        "/api/v1/documents",
        headers=headers | {"Idempotency-Key": str(uuid4())},
        data={"visibility": "DEPARTMENT"},
        files={
            "file": (
                "mixed-scan.pdf",
                scanned_pdf(native_first=True),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    await asyncio.to_thread(tasks.process_ingestion_job.run, accepted["job_id"])
    job_response = await client.get(
        "/api/v1/ingestion-jobs/" + accepted["job_id"], headers=headers
    )
    assert job_response.status_code == 200, job_response.text
    job = job_response.json()
    assert job["status"] == "SUCCEEDED", job
    assert (
        job["ocr_current_page"],
        job["ocr_pages_completed"],
        job["ocr_pages_total"],
    ) == (2, 1, 1)
    assert progress == [(2, 0, 1), (2, 1, 1)]
    doc = await db.documents.find_one({"_id": ObjectId(accepted["document_id"])})
    assert doc["status"] == "READY" and doc["active_index_version"] == 1
    namespace = f"{doc['_id']}-v1"
    assert any(
        row[2]["page"] == 2 and "20 annual leave days" in row[2]["text"]
        for row in indexed[namespace].values()
    )

    async def embedding(query):
        return [0, 0, 1]

    async def search(docs, vector):
        matches = []
        for document in docs:
            for key, values, metadata in indexed.get(
                f"{document['_id']}-v{document['active_index_version']}", {}
            ).values():
                if "20 annual leave days" in metadata["text"]:
                    matches.append(
                        (document, {"id": key, "score": 0.9, "metadata": metadata})
                    )
        return matches, 1, None

    monkeypatch.setattr(retrieval, "query_embedding", embedding)
    monkeypatch.setattr(retrieval, "search_namespaces", search)
    response = await client.post(
        "/api/v1/retrieval/search",
        headers=headers,
        json={"query": "How many annual leave days?"},
    )
    assert response.status_code == 200, response.text
    passages = response.json()["passages"]
    assert passages and all(passage["page"] == 2 for passage in passages)

    class Answer:
        async def stream(self, messages):
            yield "Employees receive 20 annual leave days [S1]."

    monkeypatch.setattr(chat_service, "GeminiProvider", Answer)
    conversation = await client.post(
        "/api/v1/conversations", headers=headers, json={"title": "OCR test"}
    )
    cid = conversation.json()["conversation_id"]
    response = await client.post(
        f"/api/v1/conversations/{cid}/messages/stream",
        headers=headers,
        json={
            "question": "How many annual leave days?",
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    stream_events = events(response)
    event, completed = stream_events[-1]
    assert event == "completed", response.text
    assert completed["is_complete"] is True
    citations = next(
        data["citations"] for event, data in stream_events if event == "citations"
    )
    assert citations[0]["page"] == 2
    assert citations[0]["document_id"] == accepted["document_id"]
    history = await client.get(f"/api/v1/conversations/{cid}/messages", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["turns"][0]["citations"][0]["page"] == 2


def seed_job(db, *, active=None):
    now = datetime.now(UTC)
    doc = {
        "_id": ObjectId(),
        "filename": "scan.pdf",
        "uploaded_by": ObjectId(),
        "blob_key": "synthetic",
        "status": "READY" if active else "QUEUED",
        "active_index_version": active,
        "chunk_count": 7 if active else 0,
    }
    job = {
        "_id": ObjectId(),
        "document_id": doc["_id"],
        "target_index_version": (active or 0) + 1,
        "status": "QUEUED",
        "stage": "VALIDATING",
        "attempt_count": 0,
        "max_attempts": 3,
        "fencing_token": 0,
        "created_at": now,
        "updated_at": now,
    }
    db.documents.insert_one(doc)
    db.ingestion_jobs.insert_one(job)
    return doc, job


def test_ocr_timeout_uses_existing_bounded_retry_budget(worker_db, monkeypatch):
    _, db = worker_db
    doc, job = seed_job(db)
    monkeypatch.setattr(tasks, "BlobServiceClient", FakeBlobService)

    def timeout(filename, content, *, ocr_progress):
        ocr_progress(1, 0, 1)
        raise ocr.ocr_error("OCR_TIMEOUT")

    def retry(**kwargs):
        raise Retry()

    monkeypatch.setattr(tasks, "extract_pages", timeout)
    monkeypatch.setattr(tasks.process_ingestion_job, "retry", retry)
    with pytest.raises(Retry):
        tasks.process_ingestion_job.run(str(job["_id"]))
    current = db.ingestion_jobs.find_one({"_id": job["_id"]})
    assert current["status"] == "QUEUED" and current["attempt_count"] == 1
    assert current["next_retry_at"] > datetime.now(UTC)
    db.ingestion_jobs.update_one(
        {"_id": job["_id"]},
        {
            "$set": {
                "attempt_count": 2,
                "next_retry_at": datetime.now(UTC) - timedelta(seconds=1),
            }
        },
    )
    tasks.process_ingestion_job.run(str(job["_id"]))
    current = db.ingestion_jobs.find_one({"_id": job["_id"]})
    assert current["status"] == "FAILED" and current["attempt_count"] == 3
    assert current["error_code"] == "OCR_TIMEOUT"
    assert db.documents.find_one({"_id": doc["_id"]})["active_index_version"] is None


@pytest.mark.parametrize(
    "code", ["OCR_UNREADABLE", "OCR_RESOURCE_LIMIT", "OCR_PAGE_LIMIT"]
)
def test_unreadable_or_over_budget_scan_preserves_prior_active_version(
    worker_db, monkeypatch, code
):
    _, db = worker_db
    doc, job = seed_job(db, active=1)
    monkeypatch.setattr(tasks, "BlobServiceClient", FakeBlobService)

    def reject(filename, content, **kwargs):
        raise ocr.ocr_error(code)

    monkeypatch.setattr(tasks, "extract_pages", reject)
    tasks.process_ingestion_job.run(str(job["_id"]))
    current = db.ingestion_jobs.find_one({"_id": job["_id"]})
    assert current["status"] == "FAILED" and current["attempt_count"] == 1
    assert current["error_code"] == code
    current_doc = db.documents.find_one({"_id": doc["_id"]})
    assert (
        current_doc["status"],
        current_doc["active_index_version"],
        current_doc["chunk_count"],
    ) == ("READY", 1, 7)


def test_ocr_progress_respects_lease_fencing(worker_db):
    _, db = worker_db
    _, job = seed_job(db)
    db.ingestion_jobs.update_one(
        {"_id": job["_id"]}, {"$set": {"status": "PROCESSING"}}
    )
    tasks._ocr_progress(db, job, 2, 0, 1)
    current = db.ingestion_jobs.find_one({"_id": job["_id"]})
    assert current["stage"] == "OCR" and current["ocr_current_page"] == 2
    db.ingestion_jobs.update_one({"_id": job["_id"]}, {"$inc": {"fencing_token": 1}})
    with pytest.raises(RuntimeError, match="INGESTION_LEASE_LOST"):
        tasks._ocr_progress(db, job, 2, 1, 1)
