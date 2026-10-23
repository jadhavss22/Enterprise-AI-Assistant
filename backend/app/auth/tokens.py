from datetime import timedelta
from secrets import token_urlsafe
from uuid import uuid4

import jwt

from app.auth.models import utcnow
from app.config import settings


def issue_access_token(user: dict, session_id: str) -> str:
    now = utcnow()
    payload = {
        "sub": user["user_id"],
        "sid": session_id,
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_access_ttl_seconds),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={"require": ["sub", "sid", "exp", "iat", "iss", "aud"]},
    )


def new_refresh_token() -> str:
    return token_urlsafe(48)


def new_session_id() -> str:
    return str(uuid4())


def refresh_digest(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()
