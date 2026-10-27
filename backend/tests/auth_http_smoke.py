"""Run inside the API container: python tests/auth_http_smoke.py.

Creates only a UUID-named disposable database and its own Redis keys.
No real accounts or tokens are printed or left in the application database.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from pymongo import MongoClient
from redis import Redis


def main():
    name = "test_auth_step_b_http_" + uuid4().hex
    os.environ["MONGODB_DATABASE"] = name
    os.environ["AUTH_RATE_PREFIX"] = name + ":"
    os.environ["REDIS_URL"] = "redis://redis:6379/15"
    from app.auth.bootstrap_admin import create_first_admin
    from app.config import settings

    password = "test-only-" + uuid4().hex
    mongo = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    redis = Redis.from_url(settings.redis_url)
    process = None
    clients = []
    try:
        asyncio.run(
            create_first_admin("smoke@example.com", "Smoke", "engineering", password)
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        url = f"http://127.0.0.1:{port}"
        first = httpx.Client(
            base_url=url, timeout=10, headers={"Origin": settings.frontend_origin}
        )
        second = httpx.Client(
            base_url=url, timeout=10, headers={"Origin": settings.frontend_origin}
        )
        clients = [first, second]
        deadline = time.monotonic() + 30
        while True:
            try:
                if first.get("/health/live").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() >= deadline or process.poll() is not None:
                raise RuntimeError("Temporary HTTP server did not start")
            time.sleep(0.2)

        def login(client):
            response = client.post(
                "/api/v1/auth/login",
                json={"email": "smoke@example.com", "password": password},
            )
            assert response.status_code == 200, "HTTP login failed"
            return response.json()

        def profile(client, data):
            return client.get(
                "/api/v1/users/me",
                headers={"Authorization": "Bearer " + data["access_token"]},
            )

        one = login(first)
        assert profile(first, one).status_code == 200
        csrf = first.get("/api/v1/auth/csrf")
        assert (
            csrf.status_code == 200 and csrf.json()["csrf_token"] == one["csrf_token"]
        )
        refreshed = first.post(
            "/api/v1/auth/refresh", headers={"X-CSRF-Token": one["csrf_token"]}
        )
        assert refreshed.status_code == 200, "HTTP refresh failed"
        one = refreshed.json()
        choices = first.get(
            "/api/v1/admin/departments",
            headers={"Authorization": "Bearer " + one["access_token"]},
        )
        assert choices.status_code == 200
        department_id = choices.json()["departments"][0]["department_id"]
        admin_headers = {"Authorization": "Bearer " + one["access_token"]}
        created = first.post(
            "/api/v1/admin/users",
            headers=admin_headers,
            json={
                "email": "employee@example.com",
                "display_name": "Employee",
                "password": password,
                "role": "EMPLOYEE",
                "department_id": department_id,
            },
        )
        assert created.status_code == 201
        employee_id = created.json()["user_id"]
        listing = first.get("/api/v1/admin/users", headers=admin_headers)
        assert listing.status_code == 200 and len(listing.json()["users"]) == 2
        with httpx.Client(
            base_url=url, timeout=10, headers={"Origin": settings.frontend_origin}
        ) as employee:
            employee_login = employee.post(
                "/api/v1/auth/login",
                json={"email": "employee@example.com", "password": password},
            )
            assert employee_login.status_code == 200
            employee_data = employee_login.json()
            assert (
                employee.get(
                    "/api/v1/admin/users",
                    headers={
                        "Authorization": "Bearer " + employee_data["access_token"]
                    },
                ).status_code
                == 403
            )
            assert (
                first.patch(
                    "/api/v1/admin/users/" + employee_id,
                    headers=admin_headers,
                    json={"status": "DISABLED"},
                ).status_code
                == 200
            )
            assert profile(employee, employee_data).status_code == 401
        last_admin = first.patch(
            "/api/v1/admin/users/" + one["user"]["user_id"],
            headers=admin_headers,
            json={"status": "DISABLED"},
        )
        assert last_admin.status_code == 409
        two = login(second)
        revoked = second.post(
            "/api/v1/auth/logout-all",
            headers={
                "X-CSRF-Token": two["csrf_token"],
                "Authorization": "Bearer " + two["access_token"],
            },
        )
        assert revoked.status_code == 204
        assert (
            profile(first, one).status_code == profile(second, two).status_code == 401
        )
        fresh = login(second)
        assert profile(second, fresh).status_code == 200
        assert (
            second.post(
                "/api/v1/auth/logout", headers={"X-CSRF-Token": fresh["csrf_token"]}
            ).status_code
            == 204
        )
        assert profile(second, fresh).status_code == 401
        assert second.post("/api/v1/auth/logout").status_code == 204
        print(
            "Real HTTP smoke passed: authentication lifecycle, Admin creation/listing, department choices, employee denial, disabling/revocation and last-Admin protection."
        )
    finally:
        for client in clients:
            client.close()
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            keys = list(redis.scan_iter(match=name + ":*"))
            if keys:
                redis.delete(*keys)
        finally:
            redis.close()
            assert name.startswith("test_auth_step_b_http_")
            mongo.drop_database(name)
            mongo.close()


if __name__ == "__main__":
    main()
