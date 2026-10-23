from datetime import UTC, datetime
from enum import StrEnum


class Role(StrEnum):
    EMPLOYEE = "employee"
    MANAGER = "manager"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


def utcnow() -> datetime:
    return datetime.now(UTC)


SEED_DEPARTMENTS = (
    {"slug": "engineering", "name": "Engineering"},
    {"slug": "hr", "name": "Human Resources"},
    {"slug": "finance", "name": "Finance"},
    {"slug": "operations", "name": "Operations"},
)
