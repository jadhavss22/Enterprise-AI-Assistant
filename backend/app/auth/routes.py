from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.audit import record_audit
from app.auth.csrf import csrf_token, require_csrf, require_origin
from app.auth.dependencies import current_user, get_db, get_redis
from app.auth.rate_limits import client_ip, rate_limit
from app.auth.schemas import AuthResponse, LoginRequest, UserPublic, public_user
from app.auth.service import (
    active_session_user,
    clear_auth_cookies,
    login,
    logout,
    logout_all,
    resolve_refresh,
    rotate_refresh,
    set_auth_cookies,
    unauthorized,
)
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["users"])


def rid(request):
    return request.state.request_id


async def throttle(request, db, redis, identity, limit, window):
    try:
        await rate_limit(redis, identity, limit, window)
    except HTTPException as error:
        await record_audit(
            db,
            "auth.rate_limit",
            "DENIED" if error.status_code == 429 else "FAILED",
            request_id=rid(request),
        )
        raise


async def browser_origin(request, db):
    try:
        require_origin(request)
    except HTTPException:
        await record_audit(db, "auth.origin", "DENIED", request_id=rid(request))
        raise


async def cookie_session(request, db, redis):
    await browser_origin(request, db)
    await throttle(
        request,
        db,
        redis,
        "cookie-ip:" + client_ip(request),
        settings.cookie_ip_rate_limit,
        60,
    )
    raw = request.cookies.get(settings.refresh_cookie_name)
    try:
        session = await resolve_refresh(db, raw)
    except HTTPException:
        await record_audit(db, "auth.cookie", "DENIED", request_id=rid(request))
        raise
    return session, raw


async def csrf_check(request, db, session):
    try:
        require_csrf(request, session)
    except HTTPException:
        await record_audit(
            db,
            "auth.csrf",
            "DENIED",
            session_id=session["_id"],
            request_id=rid(request),
        )
        raise


@router.post("/login", response_model=AuthResponse)
async def login_route(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    await browser_origin(request, db)
    await throttle(
        request,
        db,
        redis,
        "login-ip:" + client_ip(request),
        settings.login_ip_rate_limit,
        settings.login_rate_window_seconds,
    )
    await throttle(
        request,
        db,
        redis,
        "login-account:" + payload.email.strip().lower(),
        settings.login_rate_limit,
        settings.login_rate_window_seconds,
    )
    result, refresh, _ = await login(
        db, payload.email, payload.password.get_secret_value(), rid(request)
    )
    set_auth_cookies(response, refresh, result.session_expires_at)
    return result


@router.get("/csrf")
async def csrf_route(request: Request, db=Depends(get_db), redis=Depends(get_redis)):
    session, raw = await cookie_session(request, db, redis)
    await active_session_user(db, session)
    from app.auth.tokens import refresh_digest

    if session["current_refresh_hash"] != refresh_digest(raw):
        raise unauthorized()
    return {"csrf_token": csrf_token(session)}


@router.post("/refresh", response_model=AuthResponse)
async def refresh_route(
    response: Response, request: Request, db=Depends(get_db), redis=Depends(get_redis)
):
    try:
        session, raw = await cookie_session(request, db, redis)
        await csrf_check(request, db, session)
        await throttle(
            request,
            db,
            redis,
            "refresh-session:" + str(session["_id"]),
            settings.refresh_rate_limit,
            settings.refresh_rate_window_seconds,
        )
        result, refresh, _ = await rotate_refresh(
            db, str(session["_id"]), raw, rid(request)
        )
    except HTTPException as error:
        if error.status_code == 401:
            await record_audit(db, "auth.refresh", "DENIED", request_id=rid(request))
            request.state.clear_auth_cookie = True
        raise
    set_auth_cookies(response, refresh, result.session_expires_at)
    return result


@router.post("/logout", status_code=204)
async def logout_route(
    response: Response, request: Request, db=Depends(get_db), redis=Depends(get_redis)
):
    await browser_origin(request, db)
    await throttle(
        request,
        db,
        redis,
        "cookie-ip:" + client_ip(request),
        settings.cookie_ip_rate_limit,
        60,
    )
    raw = request.cookies.get(settings.refresh_cookie_name)
    try:
        session = await resolve_refresh(db, raw) if raw else None
    except HTTPException as error:
        if error.status_code != 401:
            raise
        session = None
    if session:
        await csrf_check(request, db, session)
        await logout(db, session, rid(request))
    else:
        await record_audit(db, "auth.logout", "SUCCESS", request_id=rid(request))
    clear_auth_cookies(response)


@router.post("/logout-all", status_code=204)
async def logout_all_route(
    response: Response,
    request: Request,
    user=Depends(current_user),
    db=Depends(get_db),
    redis=Depends(get_redis),
):
    session, _ = await cookie_session(request, db, redis)
    await csrf_check(request, db, session)
    if session["_id"] != user["_auth_session"]["_id"]:
        await record_audit(
            db, "auth.session_mismatch", "DENIED", request_id=rid(request)
        )
        raise HTTPException(
            403,
            detail={"code": "SESSION_MISMATCH", "message": "Session does not match"},
        )
    await throttle(
        request,
        db,
        redis,
        "logout-all:" + str(user["_id"]),
        settings.logout_all_rate_limit,
        settings.logout_all_rate_window_seconds,
    )
    await logout_all(db, user, rid(request))
    clear_auth_cookies(response)


@users_router.get("/me", response_model=UserPublic)
async def me_route(user=Depends(current_user)):
    return public_user(user)
