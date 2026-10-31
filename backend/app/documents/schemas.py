from datetime import datetime

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.documents.models import (
    DocumentStatus,
    DocumentVisibility,
    IngestionStage,
    IngestionStatus,
)


class DocumentPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    department_id: str
    visibility: DocumentVisibility
    status: DocumentStatus
    active_index_version: int | None = None
    chunk_count: int = 0
    ingestion_status: IngestionStatus | None = None
    ingestion_stage: IngestionStage | None = None
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    documents: list[DocumentPublic] = Field(default_factory=list)
    next_cursor: str | None = None


class UploadAccepted(BaseModel):
    document_id: str
    job_id: str
    status: DocumentStatus


class IngestionJobPublic(BaseModel):
    job_id: str
    document_id: str
    status: IngestionStatus
    stage: IngestionStage | None = None
    attempt_count: int
    max_attempts: int
    error_code: str | None = None
    error_message: str | None = None
    index_cleanup_status: str | None = None
    index_cleanup_error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class IngestionJobPage(BaseModel):
    jobs: list[IngestionJobPublic] = Field(default_factory=list)
    next_cursor: str | None = None


class DocumentPermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: DocumentVisibility | None = None
    allowed_user_ids: list[str] | None = None

    @field_validator("allowed_user_ids")
    @classmethod
    def valid_user_ids(cls, value):
        if value is None:
            raise ValueError("User IDs cannot be null")
        if len(value) > 100 or len(set(value)) != len(value):
            raise ValueError("Provide at most 100 unique users")
        normalized = []
        for item in value:
            if not ObjectId.is_valid(item):
                raise ValueError("Invalid user ID")
            normalized.append(str(ObjectId(item)))
        return normalized

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one permission field")
        return self


class DocumentPermissionsPublic(BaseModel):
    visibility: DocumentVisibility
    allowed_user_ids: list[str] = Field(default_factory=list)


class DocumentShareTarget(BaseModel):
    user_id: str
    display_name: str
    role: str


class DocumentShareTargetPage(BaseModel):
    users: list[DocumentShareTarget] = Field(default_factory=list)
