# Authentication Step C

Completed 27 October 2026. Admin user management and authorization are implemented and verified. The complete backend suite passes with 96 tests, including real MongoDB replica-set transactions and Redis. The real HTTP smoke verifies both authentication and Admin operations. Frontend forms, session coordination and interactive browser acceptance remain Step D.

## Endpoints and editable fields

All Admin endpoints require an explicit bearer access token and current Admin authorization. A refresh cookie alone grants no Admin API access. Employee and Manager tokens receive HTTP 403.

| Method and path | Contract |
|---|---|
| POST /api/v1/admin/users | email, display_name, password, role (default EMPLOYEE), department_id; HTTP 201 safe user |
| GET /api/v1/admin/users | Cursor list; default limit 20, maximum 100; optional role, status and department_id filters |
| PATCH /api/v1/admin/users/{id} | Nonempty subset of display_name, role, status and department_id; HTTP 200 safe user |
| GET /api/v1/admin/departments | Active department IDs/codes/names for the agreed user-creation selector; read-only |

Role/status inputs use uppercase enums (EMPLOYEE, MANAGER, ADMIN; ACTIVE, DISABLED). Safe user responses preserve lowercase frontend role/status values and string IDs. Creation uses department_id, replacing the initial unfinished department_slug API contract; the operator bootstrap CLI still accepts a convenient department code through --department-slug.

Creation normalizes email and enforces the shared 15-128 character password policy, Argon2id hashing and an existing active department. Passwords are hashed off the event loop before entering the retryable transaction. The database unique email index is the final duplicate guard; a normalized duplicate returns 409.

PATCH rejects unknown fields, explicit nulls and an empty body. Email, password_hash, password, created_by and token_version cannot be mass-assigned. Invalid IDs return 422, an unknown target returns 404, and an invalid/inactive department returns 422. Password reset, registration, user deletion and department administration are outside this slice.

Pagination sorts by created_at then ObjectId, both descending. The cursor is signed and bound to the selected filters; tampering or changing filters returns 422. Equal timestamps are covered by tests. Pagination is stable keyset traversal, not a cross-request snapshot: later inserts may appear on a fresh first page. User list queries exclude password hashes before serialization.

## Session invalidation

Actual role, status or department changes increment the target's token_version and revoke older session records in the same transaction. Existing access and refresh tokens stop working. Late-inserted older-version sessions remain unusable because Step B checks the current user version. Re-enabling a user requires a fresh sign-in; old tokens do not regain access.

Name-only changes and no-op updates do not revoke sessions. Admins may change their own privileges only while another active Admin remains. Such a successful change invalidates their session too, so the frontend must clear its state and require sign-in in Step D.

## Last active Admin and transaction design

An active Admin cannot be demoted or disabled when only one active Admin remains; the API returns 409 LAST_ACTIVE_ADMIN. Disabled Admin accounts do not count as replacements.

Each Admin mutation first writes a shared auth_admin_guard control record inside its transaction. Concurrent mutations therefore conflict on that record. The driver retries transient write conflicts, so the next successful transaction rechecks current actor authorization and the active-Admin count. This avoids snapshot write skew when different Admin rows are changed simultaneously. The guard is database control metadata, not a lease or a Redis permission cache.

User changes, session invalidation and the success audit are committed together with snapshot read concern and majority write concern. A callback failure aborts all those writes. A known audit failure does not leave a newly created user or partially disabled account. Failed/rejected attempts are logged outside the aborted transaction when the audit store is available. All audit metadata is limited to IDs, event/outcome, request ID, changed field names and fixed reason codes; passwords, raw tokens, names and email values are omitted.

Transactions have a configured overall timeout (default 15 seconds) and a five-second commit limit. If a timeout or network failure leaves the commit result uncertain, the client must check current state before retrying; a 503 must not be treated as proof that nothing committed. No external side effects occur in retryable transaction callbacks.

The shared guard serializes Admin mutations, which is a deliberate simplicity/correctness tradeoff for the demo's low-frequency administration. It is not a throughput benchmark. Read operations are not serialized through it.

MongoDB's [atomicity documentation](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/) explains the single-document and transaction boundary. The [Motor transaction API](https://motor.readthedocs.io/en/stable/api-asyncio/asyncio_motor_client_session.html) documents callback retry behavior used here.

## Local MongoDB setup

Local MongoDB now runs as the single-node replica set rs0 so multi-document transactions work. The existing mongo_data volume is retained. infra/mongo/init-replica.js initializes the set only when needed and waits for a writable primary. The one-shot mongo-init service exits with status 0 on success; that is expected, not a crashed dependency. The API waits for it before starting. A one-node development set provides transaction capability, not multi-node availability.

```powershell
docker compose up -d --build api
docker compose ps -a
```

Inside Compose, the hostname mongo is reachable normally. Host-side Python/Compass clients should use mongodb://localhost:27017/?directConnection=true because the replica set advertises its container hostname. Do not change the ignored application .env to a host-only localhost URL for containers. Production uses its own Atlas replica-set URI and credentials.

## Defaults, verification and files

Admin writes are limited to 30 per acting Admin per minute; reads to 120 per Admin per minute. Redis performs atomic limiting, with keyed identities and Retry-After responses. Redis failures block affected operations. These are configurable starting defaults, not production capacity claims.

```powershell
$env:AUTH_TEST_MONGODB_URL = "mongodb://localhost:27017/?directConnection=true"
$env:AUTH_TEST_REDIS_URL = "redis://localhost:6379/15"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\ruff.exe check backend
.\.venv\Scripts\uv.exe lock --project backend --check --offline
docker compose exec -T api python tests/auth_http_smoke.py
```

Verification: 96 tests passed; Ruff, Compose configuration and offline lock consistency passed. The HTTP smoke passed user creation/listing, active department lookup, employee denial, disabling/session revocation, last-Admin protection and the Step B lifecycle. API liveness/readiness returned HTTP 200. MongoDB, Redis and Azurite are healthy. Tests use only disposable databases and isolated Redis keys; no real application user was created.

New modules are backend/app/auth/admin_schemas.py and admin_service.py; admin_routes.py now exposes the complete contracts. Shared credential validation, storage initialization, runtime settings and startup were updated. Test coverage is in backend/tests/test_admin_users.py, with A/B regressions retained. Docker Compose and the replica initializer provide local transaction support. No dependencies or lockfiles changed.

## Interview notes

1. Why can a count-then-update check remove the last Admin under concurrency?
2. Why can snapshot transactions still need a shared write to prevent write skew?
3. Why do role/status/department changes increment token_version as well as revoking sessions?
4. How does a transaction keep user changes and success audit records consistent?
5. Why should a retryable transaction callback avoid external side effects?
6. What does cursor pagination guarantee, and what changes across page requests?
7. Why are hidden frontend links insufficient authorization?
8. Why does a transaction timeout sometimes require checking state before retrying?

## Next step

Step D connects the Next.js frontend to login, in-memory access/CSRF state, session restoration, coordinated refresh, logout/all, role-aware navigation and the Admin user-management forms. Browser and end-to-end acceptance follows. Uploads, ingestion and RAG start only after that authentication slice is verified.
