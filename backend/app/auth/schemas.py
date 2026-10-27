import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.config import settings


class LoginRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: SecretStr = Field(exclude=True)

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Invalid password length")
        return value


class UserPublic(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: Literal["employee", "manager", "admin"]
    status: Literal["active", "disabled"]
    department_id: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
    csrf_token: str
    session_expires_at: datetime


def public_user(user: dict) -> UserPublic:
    return UserPublic(
        user_id=str(user["_id"]),
        email=user["email_normalized"],
        display_name=user["name"],
        role=user["role"].lower(),
        status=user["status"].lower(),
        department_id=str(user["department_id"]),
    )


class UserCredentials(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")
    email: str
    display_name: str
    password: SecretStr = Field(exclude=True)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        value = value.strip().lower()
        if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 120:
            raise ValueError("Name must contain 1-120 characters")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if (
            not max(15, settings.password_min_length)
            <= len(value.get_secret_value())
            <= 128
        ):
            raise ValueError("Password must contain 15-128 characters")
        return value


class BootstrapInput(UserCredentials):
    department_slug: str

    @field_validator("department_slug")
    @classmethod
    def normalize_department(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
            raise ValueError("Invalid department")
        return value
