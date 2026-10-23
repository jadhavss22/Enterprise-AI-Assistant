from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.admin_routes import router as admin_users_router
from app.auth.routes import router as auth_router
from app.auth.security import validate_security_settings
from app.auth.storage import ensure_auth_storage
from app.config import settings
from app.infrastructure.health import check_dependencies


@asynccontextmanager
async def lifespan(application: FastAPI):
    validate_security_settings()
    mongo_client = AsyncIOMotorClient(settings.mongodb_url)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    application.state.mongo_client = mongo_client
    application.state.db = mongo_client[settings.mongodb_database]
    application.state.redis = redis_client
    await ensure_auth_storage(application.state.db)
    try:
        yield
    finally:
        mongo_client.close()
        await redis_client.aclose()

app = FastAPI(title="Enterprise AI Assistant API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
)
app.include_router(auth_router)
app.include_router(admin_users_router)


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"], response_model=None)
async def ready() -> JSONResponse | dict[str, object]:
    services = {
        "api": "ready",
        **await check_dependencies(settings.health_check_timeout_seconds),
    }
    is_ready = all(status == "ready" for status in services.values())
    payload = {
        "status": "ready" if is_ready else "unavailable",
        "services": services,
    }
    if not is_ready:
        return JSONResponse(status_code=503, content=payload)
    return payload

