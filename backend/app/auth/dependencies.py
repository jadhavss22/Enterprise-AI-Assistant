from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.auth.models import Role, UserStatus
from app.auth.tokens import decode_access_token

bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.db


async def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        claims = decode_access_token(credentials.credentials)
    except Exception as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from error
    user = await db.users.find_one({"user_id": claims["sub"]})
    session = await db.sessions.find_one({"session_id": claims["sid"]})
    now = datetime.now(UTC)
    if (
        not user
        or user.get("status") != UserStatus.ACTIVE.value
        or not session
        or session.get("revoked_at") is not None
        or session.get("expires_at") <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is no longer active")
    return user


def require_roles(*roles: Role):
    async def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in {role.value for role in roles}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker
