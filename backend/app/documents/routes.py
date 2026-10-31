from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.auth.dependencies import current_user, get_db, get_redis, require_roles
from app.auth.models import Role
from app.auth.rate_limits import rate_limit
from app.config import settings
from app.documents.models import DocumentVisibility, IngestionStatus
from app.documents.schemas import (
    DocumentPage,
    DocumentPermissionsPublic,
    DocumentPermissionsRequest,
    DocumentPublic,
    DocumentShareTargetPage,
    IngestionJobPage,
    IngestionJobPublic,
    UploadAccepted,
)
from app.documents.service import (
    admin_retry_job,
    audit_admin_private_access,
    create_document,
    create_reprocess_job,
    get_document_permissions,
    get_ingestion_job,
    get_visible_document,
    list_admin_ingestion_jobs,
    list_document_jobs,
    list_document_share_targets,
    list_documents,
    soft_delete_document,
    stream_document_source,
    update_document_permissions,
)
from app.documents.validation import MAX_UPLOAD_BYTES, validate_upload

router = APIRouter(prefix="/api/v1", tags=["documents"])


async def _document_read_limit(redis, actor):
    await rate_limit(
        redis, f"document-read:{actor['_id']}", settings.admin_read_rate_limit, 60
    )


async def _document_write_limit(redis, actor):
    await rate_limit(
        redis, f"document-write:{actor['_id']}", settings.admin_write_rate_limit, 60
    )


@router.post("/documents", response_model=UploadAccepted, status_code=202)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    visibility: Annotated[DocumentVisibility, Form()] = DocumentVisibility.DEPARTMENT,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await rate_limit(
        redis,
        f"document-upload:{actor['_id']}",
        settings.document_upload_rate_limit,
        3600,
    )
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    safe_name, mime_type = validate_upload(file.filename, file.content_type, content)
    return await create_document(
        db,
        redis,
        actor,
        safe_name,
        mime_type,
        content,
        visibility,
        request.state.request_id,
        str(idempotency_key),
    )


@router.get("/documents", response_model=DocumentPage)
async def get_documents(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
    actor=Depends(current_user),
    db=Depends(get_db),
):
    return await list_documents(db, actor, limit, cursor, request.state.request_id)


@router.get("/documents/{document_id}", response_model=DocumentPublic)
async def get_document(
    document_id: str, request: Request, actor=Depends(current_user), db=Depends(get_db)
):
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    from app.documents.service import serialize_document

    return serialize_document(document)


@router.get("/documents/{document_id}/source")
async def get_document_source(
    document_id: str, request: Request, actor=Depends(current_user), db=Depends(get_db)
):
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    filename = quote(document["filename"], safe="")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        stream_document_source(document["blob_key"]),
        media_type=document["mime_type"],
        headers=headers,
    )


@router.get("/documents/{document_id}/jobs", response_model=IngestionJobPage)
async def get_document_jobs(
    document_id: str, actor=Depends(current_user), db=Depends(get_db)
):
    from app.documents.access import document_object_id

    document = await db.documents.find_one({"_id": document_object_id(document_id)})
    from app.documents.access import require_visible_document

    document = require_visible_document(actor, document)
    return {"jobs": await list_document_jobs(db, actor, document)}


@router.get("/ingestion-jobs/{job_id}", response_model=IngestionJobPublic)
async def get_job(job_id: str, actor=Depends(current_user), db=Depends(get_db)):
    return await get_ingestion_job(db, actor, job_id)


@router.get(
    "/documents/{document_id}/permissions", response_model=DocumentPermissionsPublic
)
async def get_permissions(
    document_id: str,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_read_limit(redis, actor)
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    return await get_document_permissions(actor, document)


@router.get(
    "/documents/{document_id}/share-targets", response_model=DocumentShareTargetPage
)
async def get_share_targets(
    document_id: str,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_read_limit(redis, actor)
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    return await list_document_share_targets(db, actor, document)


@router.patch("/documents/{document_id}/permissions", response_model=DocumentPublic)
async def patch_document_permissions(
    document_id: str,
    payload: DocumentPermissionsRequest,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_write_limit(redis, actor)
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    updated = await update_document_permissions(
        db, actor, document, payload, request.state.request_id
    )
    from app.documents.service import serialize_document

    return serialize_document(updated)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_write_limit(redis, actor)
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    await soft_delete_document(db, actor, document, request.state.request_id)


@router.post(
    "/documents/{document_id}/reprocess", response_model=UploadAccepted, status_code=202
)
async def reprocess_document(
    document_id: str,
    request: Request,
    actor=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_write_limit(redis, actor)
    document = await get_visible_document(db, actor, document_id)
    await audit_admin_private_access(db, actor, document, request.state.request_id)
    return await create_reprocess_job(db, actor, document, request.state.request_id)


@router.post(
    "/admin/ingestion-jobs/{job_id}/retry",
    response_model=UploadAccepted,
    status_code=202,
)
async def retry_ingestion_job(
    job_id: str,
    request: Request,
    actor=Depends(require_roles(Role.ADMIN)),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await _document_write_limit(redis, actor)
    return await admin_retry_job(db, actor, job_id, request.state.request_id)


@router.get("/admin/ingestion-jobs", response_model=IngestionJobPage)
async def get_admin_ingestion_jobs(
    request: Request,
    status: IngestionStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
    actor=Depends(require_roles(Role.ADMIN)),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    return await list_admin_ingestion_jobs(
        db, redis, actor, status, limit, cursor, request.state.request_id
    )
