import hashlib
from datetime import timedelta
from secrets import token_urlsafe

import jwt
from bson import ObjectId

from app.auth.models import utcnow
from app.config import settings


def issue_access_token(user, session_id, session_expires_at=None):
    now = utcnow()
    expiry = now + timedelta(seconds=settings.jwt_access_ttl_seconds)
    if session_expires_at is not None:
        expiry = min(expiry, session_expires_at)
    payload = {
        "sub": str(user["_id"]),
        "sid": session_id,
        "role": user["role"],
        "tv": user["token_version"],
        "iat": now,
        "exp": expiry,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "typ": "access",
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_access_token(token):
    if len(token) > 4096:
        raise jwt.InvalidTokenError("Token is too long")
    claims = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=["HS256"],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        options={
            "require": ["sub", "sid", "exp", "iat", "iss", "aud", "tv", "role", "typ"]
        },
    )
    if (
        claims["typ"] != "access"
        or type(claims["tv"]) is not int
        or claims["tv"] < 0
        or not isinstance(claims["sid"], str)
        or not ObjectId.is_valid(claims["sid"])
        or not ObjectId.is_valid(claims["sub"])
        or claims["role"] not in {"EMPLOYEE", "MANAGER", "ADMIN"}
        or type(claims["exp"]) is not int
        or type(claims["iat"]) is not int
        or not 0 < claims["exp"] - claims["iat"] <= settings.jwt_access_ttl_seconds
    ):
        raise jwt.InvalidTokenError("Invalid access claims")
    return claims


def new_refresh_token():
    return token_urlsafe(48)


def new_session_id():
    return str(ObjectId())


def refresh_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()
