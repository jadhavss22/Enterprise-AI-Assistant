from fastapi import APIRouter, Depends, Request, Response
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.auth.dependencies import current_user, get_db, get_redis
from app.auth.schemas import AuthResponse, LoginRequest, UserPublic, public_user
from app.auth.service import (
    clear_auth_cookies,
    login,
    rate_limit,
    require_csrf,
    rotate_refresh,
    set_auth_cookies,
)
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=AuthResponse)
async def login_route(payload: LoginRequest, response: Response, request: Request, db=Depends(get_db), redis: Redis = Depends(get_redis)):
    await rate_limit(redis, f"login:{request.client.host if request.client else 'unknown'}:{payload.email.strip().lower()}", settings.login_rate_limit, settings.login_rate_window_seconds)
    result, refresh_token, csrf_token = await login(db, payload.email, payload.password)
    set_auth_cookies(response, refresh_token, csrf_token)
    return result


@router.post("/refresh", response_model=AuthResponse)
async def refresh_route(response: Response, request: Request, db: AsyncIOMotorDatabase = Depends(get_db), redis: Redis = Depends(get_redis)):
    require_csrf(request)
    await rate_limit(redis, f"refresh:{request.cookies.get(settings.refresh_cookie_name, '')[:16]}", settings.refresh_rate_limit, settings.refresh_rate_window_seconds)
    raw = request.cookies.get(settings.refresh_cookie_name)
    if not raw:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Refresh session required")
    try:
        # The session id is deliberately not trusted from the cookie. It is
        # carried only in the short-lived access token during normal refresh.
        # For cookie-only restoration, resolve it by its hashed refresh token.
        digest = __import__('hashlib').sha256(raw.encode()).hexdigest()
        session = await db.sessions.find_one({"$or": [{"refresh_hash": digest}, {"previous_refresh_hash": digest}]})
        if not session:
            raise ValueError("unknown refresh token")
        result, new_token, csrf_token = await rotate_refresh(db, session["session_id"], raw)
    except Exception as error:
        from fastapi import HTTPException
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(status_code=401, detail="Refresh token is invalid") from error
    set_auth_cookies(response, new_token, csrf_token)
    return result


@router.post("/logout", status_code=204)
async def logout_route(response: Response, request: Request, db=Depends(get_db)):
    require_csrf(request)
    raw = request.cookies.get(settings.refresh_cookie_name)
    if raw:
        import hashlib
        await db.sessions.update_one({"refresh_hash": hashlib.sha256(raw.encode()).hexdigest()}, {"$set": {"status": "revoked", "revoked_at": __import__('datetime').datetime.now(__import__('datetime').UTC), "revoke_reason": "logout"}})
    clear_auth_cookies(response)


@router.post("/logout-all", status_code=204)
async def logout_all_route(response: Response, request: Request, user=Depends(current_user), db=Depends(get_db), redis: Redis = Depends(get_redis)):
    require_csrf(request)
    await rate_limit(redis, f"logout-all:{user['user_id']}", settings.logout_all_rate_limit, settings.logout_all_rate_window_seconds)
    from datetime import UTC, datetime
    await db.sessions.update_many({"user_id": user["user_id"], "status": "active"}, {"$set": {"status": "revoked", "revoked_at": datetime.now(UTC), "revoke_reason": "logout_all"}})
    clear_auth_cookies(response)


@router.get("/me", response_model=UserPublic)
async def me_route(user=Depends(current_user)):
    return public_user(user)
