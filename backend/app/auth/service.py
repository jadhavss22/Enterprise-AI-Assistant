import asyncio
import re
from datetime import UTC, timedelta

from bson import ObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument

from app.auth.audit import record_audit
from app.auth.csrf import csrf_token
from app.auth.models import AuthSessionRecord, UserStatus, utcnow
from app.auth.schemas import AuthResponse, public_user
from app.auth.security import hash_password, verify_password
from app.auth.tokens import (
    issue_access_token,
    new_refresh_token,
    new_session_id,
    refresh_digest,
)
from app.config import settings

# Unknown and disabled accounts still perform Argon2 verification.
DUMMY_HASH = hash_password("non-account dummy credential for timing equalization")


def unauthorized():
    return HTTPException(
        401,
        detail={
            "code": "AUTH_INVALID",
            "message": "Authentication is invalid or expired",
        },
    )


def auth_response(user, session):
    remaining = min(
        settings.jwt_access_ttl_seconds,
        int(session["expires_at"].timestamp()) - int(utcnow().timestamp()),
    )
    if remaining <= 0:
        raise unauthorized()
    return AuthResponse(
        access_token=issue_access_token(
            user, str(session["_id"]), session["expires_at"]
        ),
        expires_in=remaining,
        user=public_user(user),
        csrf_token=csrf_token(session),
        session_expires_at=session["expires_at"],
    )


def session_matches_user(session, user):
    return bool(
        user
        and session
        and user.get("status") == UserStatus.ACTIVE.value
        and session.get("user_id") == user["_id"]
        and session.get("revoked_at") is None
        and session["expires_at"] > utcnow()
        and session.get("token_version") == user["token_version"]
        and session.get("role_at_login") == user["role"]
        and session.get("department_at_login") == user["department_id"]
    )


async def active_session_user(db, session):
    user = await db.users.find_one({"_id": session["user_id"]}) if session else None
    if not session_matches_user(session, user):
        raise unauthorized()
    return user


async def login(db, email, password, request_id="internal"):
    user = await db.users.find_one({"email_normalized": email.strip().lower()})
    valid = await asyncio.to_thread(
        verify_password, user["password_hash"] if user else DUMMY_HASH, password
    )
    if not valid or not user or user.get("status") != UserStatus.ACTIVE.value:
        await record_audit(db, "auth.login", "DENIED", request_id=request_id)
        raise unauthorized()
    now = utcnow()
    # Conditional user write is the login epoch boundary against logout-all.
    current = await db.users.find_one_and_update(
        {
            "_id": user["_id"],
            "status": "ACTIVE",
            "token_version": user["token_version"],
            "role": user["role"],
            "department_id": user["department_id"],
        },
        {"$set": {"last_login_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not current:
        await record_audit(db, "auth.login", "DENIED", request_id=request_id)
        raise unauthorized()
    token = new_refresh_token()
    session = AuthSessionRecord(
        id=ObjectId(new_session_id()),
        user_id=user["_id"],
        current_refresh_hash=refresh_digest(token),
        token_version=user["token_version"],
        role_at_login=user["role"],
        department_at_login=user["department_id"],
        expires_at=now + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
    ).bson()
    await db.auth_sessions.insert_one(session)
    try:
        await active_session_user(db, session)
        # Re-read session too: logout-all can revoke it after insertion.
        await active_session_user(
            db, await db.auth_sessions.find_one({"_id": session["_id"]})
        )
        await record_audit(
            db,
            "auth.login",
            "SUCCESS",
            user_id=user["_id"],
            session_id=session["_id"],
            request_id=request_id,
        )
    except HTTPException:
        await revoke_session(db, session, "login_incomplete")
        raise
    result = auth_response(current, session)
    return result, token, result.csrf_token


async def resolve_refresh(db, raw):
    if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9_-]{64}", raw):
        raise unauthorized()
    digest = refresh_digest(raw)
    session = await db.auth_sessions.find_one(
        {"$or": [{"current_refresh_hash": digest}, {"consumed_refresh_hashes": digest}]}
    )
    if not session:
        raise unauthorized()
    return session


async def revoke_session(db, session, reason):
    await db.auth_sessions.update_many(
        {"token_family_id": session["token_family_id"], "revoked_at": None},
        {"$set": {"revoked_at": utcnow(), "revoke_reason": reason}},
    )


async def rotate_refresh(db, session_id, refresh_token, request_id="internal"):
    # Resolve by token fingerprint too: knowing another session ID is insufficient.
    session = await resolve_refresh(db, refresh_token)
    if str(session["_id"]) != session_id:
        raise unauthorized()
    await active_session_user(db, session)
    old = refresh_digest(refresh_token)
    if old != session["current_refresh_hash"]:
        await revoke_session(db, session, "refresh_reuse")
        await record_audit(
            db,
            "auth.refresh_reuse",
            "DENIED",
            user_id=session["user_id"],
            session_id=session["_id"],
            request_id=request_id,
        )
        raise unauthorized()
    if session["rotation_counter"] >= settings.max_refresh_rotations:
        await revoke_session(db, session, "rotation_limit")
        await record_audit(
            db,
            "auth.rotation_limit",
            "DENIED",
            user_id=session["user_id"],
            session_id=session["_id"],
            request_id=request_id,
        )
        raise unauthorized()
    new_token = new_refresh_token()
    updated = await db.auth_sessions.find_one_and_update(
        {
            "_id": session["_id"],
            "current_refresh_hash": old,
            "revoked_at": None,
            "expires_at": {"$gt": utcnow()},
            "rotation_counter": {"$lt": settings.max_refresh_rotations},
        },
        {
            "$set": {
                "current_refresh_hash": refresh_digest(new_token),
                "last_used_at": utcnow(),
            },
            "$push": {"consumed_refresh_hashes": old},
            "$inc": {"rotation_counter": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        # CAS loser is strict replay. No grace window or second usable token.
        latest = await db.auth_sessions.find_one({"_id": session["_id"]})
        if latest and old in latest.get("consumed_refresh_hashes", []):
            await revoke_session(db, latest, "refresh_reuse")
            await record_audit(
                db,
                "auth.refresh_reuse",
                "DENIED",
                user_id=session["user_id"],
                session_id=session["_id"],
                request_id=request_id,
            )
        raise unauthorized()
    try:
        user = await active_session_user(db, updated)
        await record_audit(
            db,
            "auth.refresh",
            "SUCCESS",
            user_id=user["_id"],
            session_id=updated["_id"],
            request_id=request_id,
        )
        # Covers replay/logout occurring while the audit was written.
        await active_session_user(
            db, await db.auth_sessions.find_one({"_id": updated["_id"]})
        )
    except HTTPException:
        await revoke_session(db, updated, "refresh_incomplete")
        raise
    result = auth_response(user, updated)
    return result, new_token, result.csrf_token


async def logout(db, session, request_id):
    await revoke_session(db, session, "logout")
    await record_audit(
        db,
        "auth.logout",
        "SUCCESS",
        user_id=session["user_id"],
        session_id=session["_id"],
        request_id=request_id,
    )


async def logout_all(db, user, request_id):
    # Epoch invalidates old sessions even if a concurrent insert misses the sweep.
    updated = await db.users.find_one_and_update(
        {
            "_id": user["_id"],
            "status": "ACTIVE",
            "token_version": user["token_version"],
        },
        {"$inc": {"token_version": 1}, "$set": {"updated_at": utcnow()}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise unauthorized()
    await db.auth_sessions.update_many(
        {
            "user_id": user["_id"],
            "token_version": {"$lt": updated["token_version"]},
            "revoked_at": None,
        },
        {"$set": {"revoked_at": utcnow(), "revoke_reason": "logout_all"}},
    )
    await record_audit(
        db, "auth.logout_all", "SUCCESS", user_id=user["_id"], request_id=request_id
    )


def set_auth_cookies(response, refresh_token, expires_at):
    seconds = max(0, int(expires_at.timestamp()) - int(utcnow().timestamp()))
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=seconds,
        expires=expires_at.astimezone(UTC),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth",
    )


def clear_auth_cookies(response):
    response.delete_cookie(
        settings.refresh_cookie_name,
        path="/api/v1/auth",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    # Remove the obsolete readable CSRF cookie from the initial WIP implementation.
    response.delete_cookie(settings.csrf_cookie_name, path="/api/v1/auth")
