from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from app.auth.dependencies import get_db, require_roles
from app.auth.models import Role, UserStatus
from app.auth.schemas import UserPublic, public_user
from app.auth.security import hash_password

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])


class CreateUserRequest(BaseModel):
    email: str
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12)
    role: Role = Role.EMPLOYEE
    department_slug: str


async def audit(db: AsyncIOMotorDatabase, actor: dict, event_type: str, target_user_id: str, result: str) -> None:
    await db.audit_events.insert_one(
        {
            "event_type": event_type,
            "actor_user_id": actor["user_id"],
            "target_user_id": target_user_id,
            "result": result,
            "created_at": datetime.now(UTC),
            "metadata": {},
        }
    )


@router.post("", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: CreateUserRequest,
    actor: dict = Depends(require_roles(Role.ADMIN)),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    email = payload.email.strip().lower()
    department = await db.departments.find_one({"slug": payload.department_slug.strip().lower(), "active": True})
    if not department:
        raise HTTPException(status_code=422, detail="An active department is required")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = {
        "user_id": str(uuid4()),
        "email": email,
        "display_name": payload.display_name.strip(),
        "password_hash": hash_password(payload.password),
        "role": payload.role.value,
        "status": UserStatus.ACTIVE.value,
        "department_id": str(department["_id"]),
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    await db.users.insert_one(user)
    await audit(db, actor, "admin.user_created", user["user_id"], "success")
    return public_user(user)
