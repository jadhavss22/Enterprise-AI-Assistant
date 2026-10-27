from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.auth.audit import record_audit
from app.auth.service import session_matches_user, unauthorized
from app.auth.tokens import decode_access_token

bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.db


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db=Depends(get_db),
    request: Request = None,
):
    request_id = (
        getattr(request.state, "request_id", "internal") if request else "internal"
    )
    user = session = None
    try:
        if not credentials or credentials.scheme.lower() != "bearer":
            raise InvalidTokenError("Missing bearer")
        claims = decode_access_token(credentials.credentials)
        user = await db.users.find_one({"_id": ObjectId(claims["sub"])})
        session = await db.auth_sessions.find_one({"_id": ObjectId(claims["sid"])})
        if (
            not session_matches_user(session, user)
            or claims["tv"] != user["token_version"]
            or claims["role"] != user["role"]
        ):
            raise InvalidTokenError("Session invalid")
    except (InvalidTokenError, ValueError, TypeError):
        await record_audit(db, "auth.access", "DENIED", request_id=request_id)
        raise unauthorized() from None
    user["_auth_session"] = session
    return user


def require_roles(*roles):
    async def checker(
        user=Depends(current_user), db=Depends(get_db), request: Request = None
    ):
        if user["role"] not in {role.value for role in roles}:
            request_id = (
                getattr(request.state, "request_id", "internal")
                if request
                else "internal"
            )
            await record_audit(
                db,
                "auth.authorization",
                "DENIED",
                user_id=user["_id"],
                request_id=request_id,
            )
            raise HTTPException(
                403,
                detail={"code": "ACCESS_DENIED", "message": "Insufficient permissions"},
            )
        return user

    return checker
