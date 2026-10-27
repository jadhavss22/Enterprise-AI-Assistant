"""BSON persistence models. Public API serialization lives in schemas.py."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Role(StrEnum):
    EMPLOYEE = "EMPLOYEE"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


def utcnow() -> datetime:
    return datetime.now(UTC)


class MongoRecord(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True, populate_by_name=True, extra="forbid"
    )
    id: ObjectId = Field(default_factory=ObjectId, alias="_id")
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("created_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("UTC timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def bson(self) -> dict:
        return self.model_dump(by_alias=True)


class DepartmentRecord(MongoRecord):
    code: str
    name: str
    status: UserStatus = UserStatus.ACTIVE
    updated_at: datetime = Field(default_factory=utcnow)


class UserRecord(MongoRecord):
    email_normalized: str
    name: str
    password_hash: str = Field(repr=False)
    role: Role
    department_id: ObjectId
    status: UserStatus = UserStatus.ACTIVE
    token_version: int = Field(default=0, ge=0)
    created_by: ObjectId
    bootstrap_key: str | None = None
    last_login_at: datetime | None = None
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("email_normalized")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        if value != value.strip().lower():
            raise ValueError("Email must already be normalized")
        return value


class AuthSessionRecord(MongoRecord):
    user_id: ObjectId
    token_family_id: str = Field(default_factory=lambda: str(uuid4()))
    current_refresh_hash: str = Field(repr=False)
    rotation_counter: int = Field(default=0, ge=0)
    consumed_refresh_hashes: list[str] = Field(default_factory=list, repr=False)
    token_version: int = Field(default=0, ge=0)
    role_at_login: Role
    department_at_login: ObjectId
    revoked_at: datetime | None = None
    expires_at: datetime
    last_used_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def aware_expiration(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Expiration must be timezone-aware")
        return value.astimezone(UTC)


class AuditRecord(MongoRecord):
    actor_id: ObjectId | None = None
    event: str
    resource_type: str
    resource_id: ObjectId | None = None
    request_id: str
    outcome: str
    metadata: dict = Field(default_factory=dict)


SEED_DEPARTMENTS = (
    {"code": "ENGINEERING", "name": "Engineering"},
    {"code": "HR", "name": "Human Resources"},
    {"code": "FINANCE", "name": "Finance"},
)
