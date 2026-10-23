from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth.service import require_csrf
from app.auth.tokens import decode_access_token, issue_access_token
from app.config import Settings
from app.auth.security import validate_security_settings


def test_csrf_requires_matching_cookie_and_header() -> None:
    request = SimpleNamespace(cookies={"enterprise_csrf": "cookie-value"}, headers={"X-CSRF-Token": "wrong"})
    with pytest.raises(HTTPException) as error:
        require_csrf(request)
    assert error.value.status_code == 403


def test_access_token_can_be_decoded_and_has_short_lifetime_claim() -> None:
    user = {"user_id": "user-1", "role": "employee"}
    claims = decode_access_token(issue_access_token(user, "session-1"))
    assert claims["sub"] == "user-1"
    assert claims["sid"] == "session-1"
    assert claims["role"] == "employee"
    assert claims["exp"] - claims["iat"] == 600


def test_production_requires_secure_cookies_even_with_a_custom_key() -> None:
    config = Settings(app_env="production", jwt_secret_key="x" * 48, cookie_secure=False)
    with pytest.raises(RuntimeError, match="HTTPS"):
        validate_security_settings(config)
