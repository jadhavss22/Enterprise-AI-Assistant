import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.config import settings


def require_origin(request):
    expected = settings.frontend_origin.rstrip("/")
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer", "")
        try:
            parsed = urlsplit(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}" if not parsed.username else ""
        except ValueError:
            origin = ""
    if origin != expected:
        raise HTTPException(
            403,
            detail={
                "code": "ORIGIN_DENIED",
                "message": "Request origin is not allowed",
            },
        )


def csrf_token(session):
    # Stable across refresh rotation, bound to this random session family.
    value = f"csrf:{session['_id']}:{session['token_family_id']}"
    return hmac.new(
        settings.jwt_secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def require_csrf(request, session):
    require_origin(request)
    supplied = request.headers.get("x-csrf-token", "")
    if len(supplied) != 64 or not hmac.compare_digest(supplied, csrf_token(session)):
        raise HTTPException(
            403, detail={"code": "CSRF_INVALID", "message": "CSRF validation failed"}
        )
