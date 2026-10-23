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
    jwt_secret_key: str = "local-development-secret-change-me"
    jwt_issuer: str = "enterprise-ai-assistant"
    jwt_audience: str = "enterprise-ai-assistant-api"
    jwt_access_ttl_seconds: int = 600
    jwt_refresh_ttl_seconds: int = 86400
    refresh_cookie_name: str = "enterprise_refresh"
    csrf_cookie_name: str = "enterprise_csrf"
    cookie_secure: bool = False
    password_min_length: int = 12
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 900
    refresh_rate_limit: int = 30
    refresh_rate_window_seconds: int = 900
    logout_all_rate_limit: int = 5
    logout_all_rate_window_seconds: int = 900
    pinecone_api_key: str | None = None
    openai_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()

