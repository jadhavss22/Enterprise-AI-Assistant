from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    mongodb_url: str = "mongodb://mongo:27017/?replicaSet=rs0"
    mongodb_database: str = "enterprise_ai"
    redis_url: str = "redis://redis:6379/0"
    azure_blob_endpoint: str = "http://azurite:10000/devstoreaccount1"
    azure_blob_container: str = "documents"
    azure_storage_api_version: str = "2023-11-03"
    azure_storage_connection_string: str | None = Field(default=None, repr=False)
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = Field(default=None, repr=False)
    azure_openai_api_version: str = "v1"
    azure_openai_embedding_deployment: str | None = None
    azure_openai_embedding_dimensions: int = Field(default=1536, ge=1, le=8192)
    azure_openai_embedding_max_input_tokens: int = Field(default=8191, ge=1, le=32768)
    embedding_provider: Literal["local", "azure_openai"] = "local"
    local_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    local_embedding_dimensions: int = Field(default=384, ge=1, le=4096)
    local_embedding_batch_size: int = Field(default=8, ge=1, le=64)
    pinecone_index_name: str | None = None
    document_chunk_size: int = Field(default=200, ge=1, le=2000)
    document_chunk_overlap: int = Field(default=40, ge=0, le=1999)
    document_upload_rate_limit: int = Field(default=20, ge=1, le=1000)
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
