"""Operator-only bootstrap. No HTTP endpoint and no password command argument."""

import argparse
import asyncio
import getpass
import sys

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.auth.models import AuditRecord, Role, UserRecord, UserStatus
from app.auth.schemas import BootstrapInput
from app.auth.security import hash_password
from app.auth.storage import ensure_auth_storage
from app.config import settings


async def bootstrap_in_database(db, email, display_name, department_slug, password):
    data = BootstrapInput(
        email=email,
        display_name=display_name,
        department_slug=department_slug,
        password=password,
    )
    await ensure_auth_storage(db)
    # Reconcile an audit write interrupted after the atomic account insert.
    existing = await db.users.find_one({"bootstrap_key": "first-admin"})
    if existing:
        await record_bootstrap_audit(db, existing)
        await refuse_bootstrap(db, "already_completed")
    if await db.users.find_one({"role": Role.ADMIN.value}):
        await refuse_bootstrap(db, "admin_exists")
    department = await db.departments.find_one(
        {"code": data.department_slug.upper(), "status": UserStatus.ACTIVE.value}
    )
    if not department:
        await refuse_bootstrap(db, "invalid_department")
    user_id = ObjectId()
    user = UserRecord(
        id=user_id,
        email_normalized=data.email,
        name=data.display_name,
        password_hash=await asyncio.to_thread(
            hash_password, data.password.get_secret_value()
        ),
        role=Role.ADMIN,
        department_id=department["_id"],
        created_by=user_id,
        bootstrap_key="first-admin",
    ).bson()
    try:
        # Unique partial index is the claim AND the account write: no abandoned lock.
        await db.users.insert_one(user)
    except DuplicateKeyError:
        await refuse_bootstrap(db, "duplicate_account_or_bootstrap")
    await record_bootstrap_audit(db, user)
    return str(user_id)


async def refuse_bootstrap(db, reason):
    event_id = ObjectId()
    await db.audit_logs.insert_one(
        AuditRecord(
            id=event_id,
            event="admin.bootstrap",
            resource_type="user",
            request_id=str(event_id),
            outcome="DENIED",
            metadata={"reason": reason},
        ).bson()
    )
    raise ValueError("Bootstrap refused")


async def record_bootstrap_audit(db, user):
    record = AuditRecord(
        id=user["_id"],
        actor_id=user["_id"],
        event="admin.bootstrap",
        resource_type="user",
        resource_id=user["_id"],
        request_id="bootstrap:first-admin",
        outcome="SUCCESS",
        created_at=user["created_at"],
    ).bson()
    await db.audit_logs.update_one(
        {"_id": user["_id"]}, {"$setOnInsert": record}, upsert=True
    )


async def create_first_admin(email, display_name, department_slug, password):
    client = AsyncIOMotorClient(
        settings.mongodb_url, tz_aware=True, serverSelectionTimeoutMS=5000
    )
    try:
        return await bootstrap_in_database(
            client[settings.mongodb_database],
            email,
            display_name,
            department_slug,
            password,
        )
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Create the first Admin account")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--department-slug", required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error(
            "Run interactively in a terminal; piped passwords are not accepted"
        )
    password = getpass.getpass("Admin password (not echoed): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.exit(1, "Passwords do not match. No account created.\n")
    try:
        asyncio.run(
            create_first_admin(
                args.email, args.display_name, args.department_slug, password
            )
        )
    except (ValueError, RuntimeError):
        # Pydantic errors can contain inputs: never echo the exception.
        parser.exit(
            1, "Bootstrap refused: check input, department, or existing Admin.\n"
        )
    except PyMongoError:
        parser.exit(
            1,
            "Database operation failed. Rerun to reconcile a possibly completed bootstrap; no credentials will be overwritten.\n",
        )
    print("First Admin created. Credentials were not written to project files.")


if __name__ == "__main__":
    main()
