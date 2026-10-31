"""Direct API permission checks with real auth/Mongo/Redis and isolated storage/queue."""
from uuid import uuid4

import pytest
from bson import ObjectId
from test_admin_users import bearer, create, env, login  # noqa: F401

from app.documents import routes, service
from app.documents.storage import ensure_document_storage
from app.ingestion.tasks import process_ingestion_job


@pytest.mark.asyncio
async def test_document_role_and_ownership_boundaries(env, monkeypatch):  # noqa: F811 - imported pytest fixture
    await ensure_document_storage(env["db"])
    blobs = {}

    async def upload(key, content, mime):
        blobs[key] = content

    async def stream(key):
        yield blobs[key]

    monkeypatch.setattr(service, "upload_private_blob", upload)
    monkeypatch.setattr(routes, "stream_document_source", stream)
    monkeypatch.setattr(process_ingestion_job, "delay", lambda *_: None)
    accounts = {}
    for name, role in [("employee", "EMPLOYEE"), ("manager", "MANAGER"), ("other", "MANAGER")]:
        await create(env, name + "@example.com", role)
        accounts[name], _ = await login(env, name + "@example.com")
    client = env["client"]

    async def post_upload(who, visibility="PRIVATE"):
        return await client.post(
            "/api/v1/documents",
            headers={**bearer(accounts[who]), "Idempotency-Key": str(uuid4())},
            data={"visibility": visibility},
            files={"file": ("permission-test.txt", ("Synthetic permission fixture " + uuid4().hex).encode(), "text/plain")},
        )

    assert (await post_upload("employee")).status_code == 403
    assert not blobs
    assert (await post_upload("manager", "ORGANIZATION")).status_code == 403
    assert not blobs
    accepted = await post_upload("manager")
    assert accepted.status_code == 202, accepted.text
    doc_id = accepted.json()["document_id"]
    path = "/api/v1/documents/" + doc_id
    owner = bearer(accounts["manager"])
    employee = bearer(accounts["employee"])
    other = bearer(accounts["other"])
    source = await client.get(path + "/source", headers=owner)
    assert source.status_code == 200 and source.content.startswith(b"Synthetic permission fixture")
    for headers in [employee, other]:
        assert (await client.get(path + "/source", headers=headers)).status_code == 404
        assert (await client.patch(path + "/permissions", headers=headers, json={"visibility": "DEPARTMENT"})).status_code == 404
    changed = await client.patch(path + "/permissions", headers=owner, json={"visibility": "DEPARTMENT"})
    assert changed.status_code == 200, changed.text
    assert (await client.get(path + "/source", headers=employee)).status_code == 200
    # Read permission must not confer management permission.
    for headers in [employee, other]:
        assert (await client.patch(path + "/permissions", headers=headers, json={"visibility": "PRIVATE"})).status_code == 404
        assert (await client.delete(path, headers=headers)).status_code == 404
        assert (await client.post(path + "/reprocess", headers=headers)).status_code == 404
    assert (await client.patch(path + "/permissions", headers=owner, json={"visibility": "ORGANIZATION"})).status_code == 403
    for who in ["manager", "employee"]:
        headers = bearer(accounts[who])
        assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/admin/ingestion-jobs", headers=headers)).status_code == 403
    # Revocation takes effect for an already-issued employee access token.
    changed = await client.patch(path + "/permissions", headers=owner, json={"visibility": "PRIVATE", "allowed_user_ids": []})
    assert changed.status_code == 200
    assert (await client.get(path + "/source", headers=employee)).status_code == 404
    stored = await env["db"].documents.find_one({"_id": ObjectId(doc_id)})
    assert stored["visibility"] == "PRIVATE"
    assert (await client.delete(path, headers=owner)).status_code == 204
