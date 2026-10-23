from app.auth.security import hash_password, validate_security_settings, verify_password
from app.config import Settings


def test_password_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password("a-strong-development-password")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "a-strong-development-password")
    assert not verify_password(password_hash, "wrong-password")


def test_production_rejects_development_signing_defaults() -> None:
    config = Settings(app_env="production", cookie_secure=True)
    try:
        validate_security_settings(config)
    except RuntimeError as error:
        assert "signing key" in str(error)
    else:
        raise AssertionError("development JWT signing default was accepted in production")
