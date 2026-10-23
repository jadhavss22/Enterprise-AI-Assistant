from pydantic import BaseModel

from app.auth.models import Role, UserStatus


class LoginRequest(BaseModel):
    email: str
    password: str


class UserPublic(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: Role
    status: UserStatus
    department_id: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


def public_user(user: dict) -> UserPublic:
    return UserPublic(
        user_id=user["user_id"],
        email=user["email"],
        display_name=user["display_name"],
        role=user["role"],
        status=user["status"],
        department_id=user["department_id"],
    )
