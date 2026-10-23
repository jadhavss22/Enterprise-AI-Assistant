import asyncio
from collections.abc import Awaitable, Callable

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.config import settings

HealthCheck = Callable[[float], Awaitable[None]]


async def check_mongodb(timeout_seconds: float) -> None:
    client = AsyncIOMotorClient(
        settings.mongodb_url,
        serverSelectionTimeoutMS=int(timeout_seconds * 1000),
        connectTimeoutMS=int(timeout_seconds * 1000),
        socketTimeoutMS=int(timeout_seconds * 1000),
    )
    try:
        await asyncio.wait_for(client.admin.command("ping"), timeout=timeout_seconds)
    finally:
        client.close()


async def check_redis(timeout_seconds: float) -> None:
    client: Redis = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        await asyncio.wait_for(client.ping(), timeout=timeout_seconds)
    finally:
        await client.aclose()


async def check_azurite(timeout_seconds: float) -> None:
    # A response, including Azurite's expected unauthenticated 400/403 response,
    # proves that the configured blob endpoint is reachable.
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(settings.azure_blob_endpoint)
    if response.status_code >= 500:
        raise RuntimeError(f"Azurite returned HTTP {response.status_code}")


async def _run_check(
    check: HealthCheck,
    timeout_seconds: float,
) -> str:
    try:
        await asyncio.wait_for(check(timeout_seconds), timeout=timeout_seconds)
    except Exception:  # noqa: BLE001 - health probes must convert any dependency error to unavailable
        return "unavailable"
    return "ready"


async def check_dependencies(timeout_seconds: float) -> dict[str, str]:
    checks = {
        "mongodb": check_mongodb,
        "redis": check_redis,
        "azurite": check_azurite,
    }
    results = await asyncio.gather(
        *(_run_check(check, timeout_seconds) for check in checks.values())
    )
    return dict(zip(checks, results, strict=True))
