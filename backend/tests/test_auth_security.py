from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.auth.csrf import require_csrf
from app.auth.security import validate_security_settings
from app.auth.tokens import decode_access_token, issue_access_token
from app.config import Settings


def test_csrf_requires_matching_cookie_and_header() -> None:
    request = SimpleNamespace(
        headers={"origin": "http://localhost:3000", "x-csrf-token": "wrong"}
    )
    with pytest.raises(HTTPException) as error:
        require_csrf(request, {"_id": ObjectId(), "token_family_id": "test-family"})
    assert error.value.status_code == 403


def test_access_token_can_be_decoded_and_has_short_lifetime_claim() -> None:
    user = {"_id": ObjectId(), "role": "EMPLOYEE", "token_version": 0}
    claims = decode_access_token(issue_access_token(user, str(user["_id"])))
    assert claims["sub"] == str(user["_id"])
    assert claims["sid"] == str(user["_id"])
    assert claims["role"] == "EMPLOYEE"
    assert claims["exp"] - claims["iat"] == 600


def test_production_requires_secure_cookies_even_with_a_custom_key() -> None:
    config = Settings(
        app_env="production", jwt_secret_key="x" * 48, cookie_secure=False
    )
    with pytest.raises(RuntimeError, match="HTTPS"):
        validate_security_settings(config)
