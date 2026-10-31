from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.auth.admin_routes import router as admin_users_router
from app.auth.errors import install_auth_errors
from app.auth.routes import router as auth_router
from app.auth.routes import users_router
from app.auth.security import validate_security_settings
from app.auth.storage import ensure_auth_storage
from app.config import settings
from app.documents.body_limit import UploadBodyLimitMiddleware
from app.documents.routes import router as documents_router
from app.documents.storage import ensure_document_storage
from app.infrastructure.health import check_dependencies


@asynccontextmanager
async def lifespan(application: FastAPI):
    validate_security_settings()
    mongo_client = AsyncIOMotorClient(
        settings.mongodb_url,
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=5000,
    )
    redis_client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )
    application.state.mongo_client = mongo_client
    application.state.db = mongo_client[settings.mongodb_database]
    application.state.redis = redis_client
    try:
        topology = await mongo_client.admin.command("hello")
        if not topology.get("setName") and topology.get("msg") != "isdbgrid":
            raise RuntimeError(
                "Admin transactions require a MongoDB replica set; start the mongo-init Compose service"
            )
        await ensure_auth_storage(application.state.db)
        await ensure_document_storage(application.state.db)
        yield
    finally:
        mongo_client.close()
        await redis_client.aclose()


app = FastAPI(title="Enterprise AI Assistant API", version="0.1.0", lifespan=lifespan)
install_auth_errors(app)
app.add_middleware(UploadBodyLimitMiddleware)
# CORS must wrap the request/error middleware so failures reach browser clients.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin.rstrip("/")],
    allow_credentials=True,
    expose_headers=["Retry-After", "X-Request-ID"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-CSRF-Token",
        "Idempotency-Key",
    ],
)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(admin_users_router)
app.include_router(documents_router)


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
