from datetime import timedelta
from secrets import token_urlsafe
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from redis.asyncio import Redis

from app.auth.models import SessionStatus, UserStatus, utcnow
from app.auth.schemas import AuthResponse, public_user
from app.auth.security import verify_password
from app.auth.tokens import (
    issue_access_token,
    new_refresh_token,
    new_session_id,
    refresh_digest,
)
from app.config import settings


async def rate_limit(redis: Redis, key: str, limit: int, window: int) -> None:
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Rate limiter unavailable") from error
    if count > limit:
        raise HTTPException(status_code=429, detail="Too many requests")


def csrf_value(request: Request) -> str | None:
    return request.headers.get("X-CSRF-Token")


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = csrf_value(request)
    if not cookie or not header or cookie != header:
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def set_auth_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=settings.jwt_refresh_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=settings.jwt_refresh_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.refresh_cookie_name, path="/api/v1/auth")
    response.delete_cookie(settings.csrf_cookie_name, path="/api/v1/auth")


async def login(db: AsyncIOMotorDatabase, email: str, password: str) -> tuple[AuthResponse, str, str]:
    normalized = email.strip().lower()
    user = await db.users.find_one({"email": normalized})
    if not user or user.get("status") != UserStatus.ACTIVE.value or not verify_password(user["password_hash"], password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    now = utcnow()
    session_id = new_session_id()
    refresh_token = new_refresh_token()
    await db.sessions.insert_one(
        {
            "session_id": session_id,
            "family_id": str(uuid4()),
            "user_id": user["user_id"],
            "refresh_hash": refresh_digest(refresh_token),
            "status": SessionStatus.ACTIVE.value,
            "created_at": now,
            "last_rotated_at": now,
            "expires_at": now + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
            "revoked_at": None,
        }
    )
    csrf_token = token_urlsafe(32)
    return (
        AuthResponse(access_token=issue_access_token(user, session_id), expires_in=settings.jwt_access_ttl_seconds, user=public_user(user)),
        refresh_token,
        csrf_token,
    )


async def rotate_refresh(db: AsyncIOMotorDatabase, session_id: str, refresh_token: str) -> tuple[AuthResponse, str, str]:
    now = utcnow()
    old_digest = refresh_digest(refresh_token)
    new_token = new_refresh_token()
    session = await db.sessions.find_one_and_update(
        {
            "session_id": session_id,
            "refresh_hash": old_digest,
            "status": SessionStatus.ACTIVE.value,
            "revoked_at": None,
            "expires_at": {"$gt": now},
        },
        {
            "$set": {
                "refresh_hash": refresh_digest(new_token),
                "previous_refresh_hash": old_digest,
                "previous_refresh_grace_until": now + timedelta(seconds=2),
                "last_rotated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not session:
        existing = await db.sessions.find_one({"session_id": session_id})
        if existing and existing.get("previous_refresh_hash") == old_digest and existing.get("previous_refresh_grace_until", now) >= now:
            raise HTTPException(status_code=409, detail="Refresh already used; retry with the latest session state")
        if existing and existing.get("status") == SessionStatus.ACTIVE.value:
            await db.sessions.update_many(
                {"family_id": existing["family_id"], "status": SessionStatus.ACTIVE.value},
                {"$set": {"status": SessionStatus.REVOKED.value, "revoked_at": now, "revoke_reason": "refresh_reuse"}},
            )
        raise HTTPException(status_code=401, detail="Refresh token is invalid or expired")
    user = await db.users.find_one({"user_id": session["user_id"]})
    if not user or user.get("status") != UserStatus.ACTIVE.value:
        raise HTTPException(status_code=401, detail="Account is not active")
    return (
        AuthResponse(access_token=issue_access_token(user, session_id), expires_in=settings.jwt_access_ttl_seconds, user=public_user(user)),
        new_token,
        token_urlsafe(32),
    )
