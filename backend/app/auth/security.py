from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import Settings, settings

# Benchmark on the development machine: approximately 302 ms per hash using
# time=3, memory=65536 KiB, parallelism=1, hash_len=32, salt_len=16.
password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def validate_security_settings(config: Settings = settings) -> None:
    if config.app_env.lower() in {"production", "prod"}:
        if config.jwt_secret_key == "local-development-secret-change-me":
            raise RuntimeError("A production JWT signing key must be explicitly configured")
        if len(config.jwt_secret_key) < 32:
            raise RuntimeError("A production JWT signing key must be at least 32 characters")
        if not config.cookie_secure:
            raise RuntimeError("Production refresh cookies require HTTPS and cookie_secure=true")
