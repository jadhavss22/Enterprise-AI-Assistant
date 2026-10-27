# Authentication Step A

Step A establishes persistence, indexes, department seeds and operator-only first-Admin creation. Login, refresh-token security, CSRF, role-management UI and full authentication acceptance remain unfinished in subsequent steps.

## Persistence contract

- `users`: ObjectId `_id`, `email_normalized`, `name`, encoded `password_hash`, uppercase `role`/`status`, ObjectId `department_id`/`created_by`, `token_version`, and UTC timestamps.
- `departments`: ObjectId `_id`, unique uppercase `code`, `name`, uppercase status, and UTC timestamps. ENGINEERING, HR and FINANCE are seeded only if absent. Rerunning setup preserves existing names and disabled status.
- `auth_sessions`: ObjectId `_id` and `user_id`, token-family identifier, unique hashed current refresh token, rotation counter, revocation and absolute-expiration dates. The TTL index is housekeeping; API expiration checks remain required.
- `audit_logs`: ObjectId actor/resource references, event/outcome, timestamp, request ID and bounded application-selected metadata. No retention TTL has been invented.

Pydantic models validate application writes; unique MongoDB indexes handle races. These are not collection-wide JSON Schema validators and cannot constrain direct administrator writes to MongoDB. The API converts ObjectIds to strings and preserves the existing frontend's lowercase role/status values and `user_id`/`display_name` fields. Existing auth code was repointed to these collections without adding Step B features. `/api/v1/auth/me` remains an existing unfinished route; align it with `/api/v1/users/me` during Step B.

## Bootstrap behavior

The bootstrap CLI prompts twice for a hidden password. It rejects piped input and never accepts a password command-line argument. The shared policy requires 15-128 characters (or a higher configured minimum), without silent trimming, truncation or composition rules. Argon2id uses time=3, memory=65536 KiB, parallelism=1, 32-byte output and a fresh 16-byte salt. Deployment hardware still needs capacity benchmarking.

A unique partial index on `users.bootstrap_key=first-admin` makes the account insertion itself the one-time claim. Concurrent CLI attempts cannot create multiple bootstrap accounts. A crash before insertion leaves no lock. A crash after insertion but before the audit write is recovered on rerun: an idempotent success event is repaired, and the account is preserved. Successful and refused attempts use metadata-only audits. Any existing Admin, including a disabled one, blocks bootstrap. Recovering a disabled/lost Admin is a separate operator procedure; rerunning bootstrap does not bypass account controls.

Department seeding happens before credential setup. No real Admin was created during automated verification. Disposable tests use generated database names and remove only their own test databases.

## Run locally

From the repository root in PowerShell, start the API dependencies:

```powershell
docker compose up -d --build api
```

To create your real development Admin, run this yourself in an interactive terminal, replacing the example identity:

```powershell
docker compose exec api python -m app.auth.bootstrap_admin --email you@example.com --display-name "Your Name" --department-slug engineering
```

Accepted department arguments are `engineering`, `hr`, and `finance` (case-insensitive). Do not paste your password into a chat, command argument or tracked file. At this milestone the account foundation is verified; browser authentication still needs Step B/D completion.

For tests, install the locked development dependencies using your installed `uv` (the existing repository tool is `.venv/Scripts/uv.exe`):

```powershell
.\.venv\Scripts\uv.exe sync --project backend --frozen --extra dev
docker compose up -d --build api
$env:AUTH_TEST_MONGODB_URL = "mongodb://localhost:27017/?directConnection=true"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\ruff.exe check backend
```

When AUTH_TEST_MONGODB_URL is not set, real MongoDB tests explicitly skip. Setting it requires a reachable database; a connection failure fails the tests rather than being reported as a pass.

## Existing data and migration safety

The local application database was inspected using collection counts before schema alignment: users, departments, sessions, auth_sessions, audit_events and audit_logs were empty. No existing account migration or deletion was needed.

Startup checks for populated legacy `sessions`/`audit_events` or incompatible user/department fields before creating indexes or seeding. It refuses setup with an explicit migration-required error. It does not drop, rename or rewrite legacy data. For another environment with old data: stop auth writers, back up the database, map user/department ObjectIds and references, reconcile normalized-email conflicts, migrate roles/statuses and session/audit references, then validate offline before enabling writers. No automatic migration is supplied or run by this slice.

## Verification

26 October 2026: backend suite passed with 22 tests, including real MongoDB. Ruff passed. Coverage includes concurrent bootstrap, repeat setup, disabled/missing departments, duplicate email, disabled existing Admin, interrupted audit recovery, password hashing/redaction, CLI input/error safety, legacy-data preservation, unique user/session indexes, and the existing login/current-user/refresh persistence compatibility flow. The full auth security acceptance suite remains work for Steps B-D.

## Learning notes

In MERN, a Mongoose schema validates an application write. Pydantic fills a similar role here. An application-level `find` followed by `insert` can race in either stack; only a database uniqueness constraint arbitrates competing inserts atomically. Idempotent setup means rerunning it produces the same intended state without resetting user changes. Password hashing is intentionally expensive and one-way; verification recomputes against the stored encoded hash and salt.

Final runtime checks: rebuilt API image successfully with frozen dependencies; API, MongoDB, Redis and Azurite are healthy; /health/live and /health/ready both returned HTTP 200. Bootstrap CLI --help works inside the API container. Application database contains users=0, departments=3, auth_sessions=0, audit_logs=0. No disposable Step A databases remain. Offline uv lock consistency and git diff whitespace checks passed. Frontend/worker were not restarted for this backend-only step.
