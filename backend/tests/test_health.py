import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_liveness() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_returns_503_when_a_required_dependency_is_unavailable(
    monkeypatch,
) -> None:
    async def unavailable_dependencies(timeout_seconds: float) -> dict[str, str]:
        return {"mongodb": "ready", "redis": "unavailable", "azurite": "ready"}

    monkeypatch.setattr("app.main.check_dependencies", unavailable_dependencies)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "services": {
            "api": "ready",
            "mongodb": "ready",
            "redis": "unavailable",
            "azurite": "ready",
        },
    }


@pytest.mark.asyncio
async def test_readiness_recovers_when_the_dependency_returns(monkeypatch) -> None:
    dependency_states = iter(
        [
            {"mongodb": "ready", "redis": "unavailable", "azurite": "ready"},
            {"mongodb": "ready", "redis": "ready", "azurite": "ready"},
        ]
    )

    async def changing_dependencies(timeout_seconds: float) -> dict[str, str]:
        return next(dependency_states)

    monkeypatch.setattr("app.main.check_dependencies", changing_dependencies)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        unavailable = await client.get("/health/ready")
        recovered = await client.get("/health/ready")

    assert unavailable.status_code == 503
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "ready"

