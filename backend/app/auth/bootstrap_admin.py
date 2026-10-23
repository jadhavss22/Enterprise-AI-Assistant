import argparse
import asyncio
import getpass
import re
import sys
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

from app.auth.models import Role, UserStatus, utcnow
from app.auth.security import hash_password
from app.auth.storage import ensure_auth_storage
from app.config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first Admin account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--department-slug", required=True)
    return parser.parse_args()


async def create_first_admin(email: str, display_name: str, department_slug: str, password: str) -> None:
    if len(password) < settings.password_min_length:
        raise ValueError(f"Password must be at least {settings.password_min_length} characters")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("A valid email address is required")

    client = AsyncIOMotorClient(settings.mongodb_url)
    try:
        db = client[settings.mongodb_database]
        await ensure_auth_storage(db)
        if await db.users.find_one({"role": Role.ADMIN.value, "status": UserStatus.ACTIVE.value}):
            raise ValueError("An active Admin already exists; refusing to create another bootstrap account")
        department = await db.departments.find_one({"slug": department_slug, "active": True})
        if not department:
            raise ValueError("The requested active department does not exist")
        now = utcnow()
        await db.users.insert_one(
            {
                "user_id": str(uuid4()),
                "email": email.strip().lower(),
                "display_name": display_name.strip(),
                "password_hash": hash_password(password),
                "role": Role.ADMIN.value,
                "status": UserStatus.ACTIVE.value,
                "department_id": str(department["_id"]),
                "created_at": now,
                "updated_at": now,
            }
        )
    finally:
        client.close()


def main() -> None:
    args = parse_args()
    password = getpass.getpass("Admin password (not echoed): ")
    try:
        asyncio.run(create_first_admin(args.email, args.display_name, args.department_slug, password))
    except (ValueError, RuntimeError) as error:
        print(f"Bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    print("First Admin account created. The password was not stored in project files.")


if __name__ == "__main__":
    main()
