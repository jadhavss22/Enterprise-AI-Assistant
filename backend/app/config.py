from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    mongodb_url: str = "mongodb://mongo:27017"
    mongodb_database: str = "enterprise_ai"
    redis_url: str = "redis://redis:6379/0"
    azure_blob_endpoint: str = "http://azurite:10000/devstoreaccount1"
    azure_blob_container: str = "documents"
    health_check_timeout_seconds: float = 2.0
    frontend_origin: str = "http://localhost:3000"
    jwt_secret_key: str = Field(
        default="local-development-secret-change-me", repr=False
    )
    jwt_issuer: str = "enterprise-ai-assistant"
    jwt_audience: str = "enterprise-ai-assistant-api"
    jwt_access_ttl_seconds: int = Field(default=600, ge=1, le=600)
    jwt_refresh_ttl_seconds: int = Field(default=86400, ge=1, le=86400)
    refresh_cookie_name: str = "enterprise_refresh"
    csrf_cookie_name: str = "enterprise_csrf"
    cookie_secure: bool = False
    password_min_length: int = Field(default=15, ge=15, le=128)
    login_ip_rate_limit: int = Field(default=30, ge=1)
    cookie_ip_rate_limit: int = Field(default=120, ge=1)
    auth_rate_prefix: str = "enterprise_ai:auth:"
    max_refresh_rotations: int = Field(default=2048, ge=1, le=4096)
    login_rate_limit: int = Field(default=5, ge=1)
    login_rate_window_seconds: int = Field(default=900, ge=1)
    refresh_rate_limit: int = Field(default=30, ge=1)
    refresh_rate_window_seconds: int = Field(default=60, ge=1)
    logout_all_rate_limit: int = Field(default=5, ge=1)
    logout_all_rate_window_seconds: int = Field(default=900, ge=1)
    admin_write_rate_limit: int = Field(default=30, ge=1)
    admin_read_rate_limit: int = Field(default=120, ge=1)
    admin_transaction_timeout_seconds: int = Field(default=15, ge=1, le=60)
    pinecone_api_key: str | None = None
    openai_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
