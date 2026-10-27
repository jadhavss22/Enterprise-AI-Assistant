from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth.admin_schemas import (
    CreateUserRequest,
    DepartmentsPage,
    UpdateUserRequest,
    UsersPage,
    object_id_string,
)
from app.auth.admin_service import audit_admin, create_user, list_users, update_user
from app.auth.dependencies import get_db, get_redis, require_roles
from app.auth.models import Role, UserStatus
from app.auth.rate_limits import rate_limit
from app.auth.schemas import UserPublic
from app.config import settings

router = APIRouter(prefix="/api/v1/admin", tags=["admin-users"])
admin_only = require_roles(Role.ADMIN)


async def admin_limit(request, db, redis, actor, write=False):
    try:
        await rate_limit(
            redis,
            f"admin-{'write' if write else 'read'}:{actor['_id']}",
            settings.admin_write_rate_limit
            if write
            else settings.admin_read_rate_limit,
            60,
        )
    except HTTPException as error:
        await audit_admin(
            db,
            actor["_id"],
            "admin.rate_limit",
            "DENIED" if error.status_code == 429 else "FAILED",
            request.state.request_id,
        )
        raise


@router.post("/users", response_model=UserPublic, status_code=201)
async def create_user_route(
    payload: CreateUserRequest,
    request: Request,
    actor=Depends(admin_only),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await admin_limit(request, db, redis, actor, write=True)
    return await create_user(db, actor, payload, request.state.request_id)


@router.get("/users", response_model=UsersPage)
async def list_users_route(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=1024),
    role: Role | None = None,
    status: UserStatus | None = None,
    department_id: str | None = None,
    actor=Depends(admin_only),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await admin_limit(request, db, redis, actor)
    filters = {}
    if role is not None:
        filters["role"] = role.value
    if status is not None:
        filters["status"] = status.value
    if department_id is not None:
        from bson import ObjectId

        try:
            filters["department_id"] = ObjectId(object_id_string(department_id))
        except ValueError:
            raise HTTPException(
                422,
                detail={
                    "code": "DEPARTMENT_INVALID",
                    "message": "Invalid department ID",
                },
            ) from None
    return await list_users(db, actor, limit, cursor, filters, request.state.request_id)


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user_route(
    user_id: str,
    payload: UpdateUserRequest,
    request: Request,
    actor=Depends(admin_only),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await admin_limit(request, db, redis, actor, write=True)
    try:
        user_id = object_id_string(user_id)
    except ValueError:
        raise HTTPException(
            422, detail={"code": "USER_ID_INVALID", "message": "Invalid user ID"}
        ) from None
    return await update_user(db, actor, user_id, payload, request.state.request_id)


@router.get("/departments", response_model=DepartmentsPage)
async def departments_route(
    request: Request,
    actor=Depends(admin_only),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    # Read-only choices for the agreed user-creation form, not department management.
    await admin_limit(request, db, redis, actor)
    rows = (
        await db.departments.find({"status": "ACTIVE"}, {"name": 1, "code": 1})
        .sort("code", 1)
        .to_list(length=100)
    )
    await audit_admin(
        db,
        actor["_id"],
        "admin.departments_listed",
        "SUCCESS",
        request.state.request_id,
        resource_type="department",
    )
    return {
        "departments": [
            {"department_id": str(row["_id"]), "code": row["code"], "name": row["name"]}
            for row in rows
        ]
    }
