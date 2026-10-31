import logging
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException


def install_auth_errors(app):
    @app.middleware("http")
    async def request_metadata(request: Request, call_next):
        # Generate correlation IDs; arbitrary client strings can contain secrets.
        request.state.request_id = str(uuid4())
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary must sanitize unexpected failures
            # Log only exception type and server-generated correlation ID, never payloads.
            logging.getLogger(__name__).error(
                "Unhandled API error type=%s request_id=%s",
                type(exc).__name__, request.state.request_id,
            )
            response = error_response(
                request, 500, "INTERNAL_ERROR", "The server could not complete this request"
            )
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith(
            ("/api/v1/auth", "/api/v1/users", "/api/v1/admin", "/api/v1/documents", "/api/v1/ingestion-jobs")
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    def error_response(request, status, code, message, headers=None):
        response = JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": getattr(request.state, "request_id", "internal"),
                }
            },
            headers=headers,
        )
        if getattr(request.state, "clear_auth_cookie", False):
            from app.auth.service import clear_auth_cookies

            clear_auth_cookies(response)
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return error_response(
            request,
            exc.status_code,
            detail.get("code", f"HTTP_{exc.status_code}"),
            detail.get("message", str(exc.detail)),
            exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never serialize exc.errors(): password inputs can appear in it.
        return error_response(
            request, 422, "VALIDATION_ERROR", "Request validation failed"
        )

    async def dependency_error(request, exc):
        return error_response(
            request, 503, "DEPENDENCY_UNAVAILABLE", "Required service unavailable"
        )

    app.add_exception_handler(PyMongoError, dependency_error)
    app.add_exception_handler(RedisError, dependency_error)
