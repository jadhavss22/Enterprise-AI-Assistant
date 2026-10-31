import os
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from httpx import ASGITransport, AsyncClient

from app.auth.errors import install_auth_errors
from app.documents import blob_store


@pytest.mark.asyncio
async def test_unexpected_server_error_is_safe_json_with_cors():
    app = FastAPI()
    install_auth_errors(app)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True)

    @app.get("/api/v1/documents/failure-probe")
    async def fail():
        raise RuntimeError("private-error-details-must-not-escape")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/documents/failure-probe", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "private-error-details" not in response.text


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LOCAL_AZURITE_TEST") != "1", reason="Explicit local Azurite test opt-in required")
async def test_real_azurite_upload_download_content_type_and_cleanup(monkeypatch):
    # Published emulator credential only. Never target a configured cloud account.
    connection = blob_store._AZURITE_CONNECTION.replace("azurite:10000", "localhost:10000")
    monkeypatch.setattr(blob_store.settings, "azure_storage_connection_string", connection)
    key = f"qa-regression/{uuid4().hex}.txt"
    body = b"Synthetic upload transport regression fixture."
    try:
        await blob_store.upload_private_blob(key, body, "text/plain")
        async with blob_store.blob_container() as container:
            blob = container.get_blob_client(key)
            properties = await blob.get_blob_properties()
            content = await (await blob.download_blob()).readall()
        assert content == body
        assert properties.content_settings.content_type == "text/plain"
    finally:
        await blob_store.delete_blob(key)
    async with blob_store.blob_container() as container:
        assert not await container.get_blob_client(key).exists()
