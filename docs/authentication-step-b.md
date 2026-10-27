# Authentication Step B

Completed and verified 27 October 2026. This milestone covers backend authentication APIs. Admin account-management completion is Step C; browser session integration, refresh coordination, navigation and interactive acceptance are Step D. Uploads and RAG remain outside this slice.

## API contract

| Endpoint | Required inputs | Result |
|---|---|---|
| POST /api/v1/auth/login | Allowed Origin (or validated Referer), JSON email/password | Access JWT, safe user, CSRF token, session expiry; HttpOnly refresh cookie |
| GET /api/v1/auth/csrf | Allowed origin and current refresh cookie | Session-bound CSRF token, without rotating credentials |
| POST /api/v1/auth/refresh | Allowed origin, refresh cookie, X-CSRF-Token | Rotated refresh cookie and new access JWT |
| POST /api/v1/auth/logout | Allowed origin; CSRF header when a known session cookie is present | Revoke the current family, clear cookies, HTTP 204; missing/unknown cookies are idempotent no-ops |
| POST /api/v1/auth/logout-all | Current bearer token plus matching session cookie and CSRF header | Increment user token version, revoke older sessions, clear cookies, HTTP 204 |
| GET /api/v1/users/me | Bearer access token | Safe current user with string IDs and lowercase API role/status |

Successful login and refresh return `access_token`, `token_type`, `expires_in`, `user`, `csrf_token`, and `session_expires_at`. Access tokens and CSRF tokens belong in browser memory. The refresh cookie is HttpOnly, host-only, SameSite=Lax and scoped to /api/v1/auth. The previous /api/v1/auth/me endpoint is replaced by the SDD's /api/v1/users/me.

Authentication responses and errors use Cache-Control: no-store. Errors use `{error:{code,message,request_id}}` and server-generated correlation IDs. Validation errors do not echo passwords or request bodies. HTTP 401 is invalid/expired authentication, 403 is authorization/origin/CSRF failure, 422 is input validation, 429 is throttling (with Retry-After), and 503 is required infrastructure/audit unavailability.

## Session and concurrency behavior

- Access JWT lifetime is ten minutes, capped by the remaining session lifetime. Signature, algorithm, issuer, audience, type, IDs, role, token version, issued-at and expiry are validated.
- Session lifetime is an absolute one day. Refreshing never extends the database deadline, JWT deadline beyond it, or cookie expiry. UTC dates read from MongoDB are normalized before cookie formatting.
- Every protected request checks the current MongoDB account and session, ownership, expiry, revocation, token version, role and department snapshots. A disabled account, changed role/department, revoked session or stale token version invalidates access immediately at the next authorization check. Already-authorized in-flight work may finish.
- An atomic MongoDB compare-and-swap rotates a refresh token once. Consumed SHA-256 fingerprints are retained in the session until its expiry and indexed for replay lookup. Random unknown tokens cannot revoke somebody else's session.
- Strict replay policy: replay of a consumed token revokes the family. There is no two-second grace window. Concurrent duplicate refresh requests cause at most one rotation, and can invalidate the winner's token as well. Step D must coordinate refresh within and across browser tabs.
- History is bounded by MAX_REFRESH_ROTATIONS (default 2048; maximum configurable 4096). Reaching the cap revokes the family and requires sign-in; it never truncates replay evidence before expiry.
- Logout-all increments the user's token version before its revocation sweep. Sessions inserted late with an older version remain unusable. A legitimate login beginning after the version change can establish a new session.
- Session TTL cleanup is eventual housekeeping. Authorization always checks expiration synchronously.

## CSRF, origins and cookies

Login validates the browser origin to prevent login CSRF. Cookie-authenticated writes require an exact allowed origin (or validated Referer fallback) and a session-bound HMAC CSRF token. Missing provenance, foreign origins and another session's CSRF token are rejected. The CSRF token is returned by login/refresh and restored through GET /auth/csrf; no readable CSRF cookie is required.

Local frontend http://localhost:3000 and API http://localhost:8000 use credentialed fetch and explicit CORS with X-CSRF-Token allowed. Development HTTP may use COOKIE_SECURE=false. Staging/production require HTTPS frontend origin, Secure cookies and an explicitly configured signing key; development signing defaults are rejected. SameSite=Lax assumes a same-site frontend/API deployment. Cross-site hosting needs a separate reviewed cookie configuration. Production key storage/rotation and audit retention remain deployment prerequisites.

Forwarded headers are not parsed by the limiter. Configure trusted proxy addresses at the ASGI/reverse-proxy layer before deployment. Password hashing runs off the async event loop, and unknown/disabled accounts still perform password verification before a generic failure response.

## Rate limits and audit failure policy

Redis Lua makes increment/expiry atomic. Redis keys contain keyed hashes of rate identities rather than raw emails, IPs or token fragments. Starting configurable limits are:

- Login: 5 attempts per normalized account and 30 per client IP per 15 minutes.
- Refresh: 30 per session per minute.
- Cookie endpoints: aggregate 120 requests per client IP per minute.
- Logout-all: 5 per user per 15 minutes.

Limits return 429 plus Retry-After and recover when the key expires. These are starting implementation defaults, not measured production capacity guarantees. Redis failure blocks the affected auth mutation with 503.

Audit events cover login success/denial, refresh success/denial and reuse, rotation limits, logout/all, origin/CSRF failures, role denials, cookie failures, rate-limit events and session mismatch. Records contain event/outcome, IDs, UTC time and request ID; passwords, JWTs, refresh tokens, CSRF tokens and raw request bodies are omitted.

Login/refresh audit failure prevents returning credentials and revokes the session when possible. Revocation operations apply their security state change before recording success; if the audit write then fails, the API reports 503 even though revocation may already have taken effect. No cross-collection transactional audit guarantee is claimed. Database outages fail closed; deployment audit durability/tamper resistance needs further production design.

## Files and dependencies

The auth service, routes, dependency checks, JWT helpers, models/storage, settings and FastAPI startup were updated. Focused helpers are `audit.py`, `csrf.py`, `rate_limits.py` and `errors.py` under backend/app/auth/. `.env.example` lists the local defaults. Existing locked dependencies were sufficient; no packages or lockfiles changed.

Tests are in backend/tests/test_sessions.py with Step A regressions retained. backend/tests/auth_http_smoke.py runs a temporary real HTTP server inside the API container, using its own disposable MongoDB database and Redis key prefix. Tests never create real application accounts.

## Verification commands

From the repository root in PowerShell:

```powershell
docker compose up -d --build api
$env:AUTH_TEST_MONGODB_URL = "mongodb://localhost:27017/?directConnection=true"
$env:AUTH_TEST_REDIS_URL = "redis://localhost:6379/15"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\ruff.exe check backend
.\.venv\Scripts\uv.exe lock --project backend --check --offline
docker compose up -d --build api
docker compose exec -T api python tests/auth_http_smoke.py
Invoke-WebRequest http://localhost:8000/health/live
Invoke-WebRequest http://localhost:8000/health/ready
```

The backend suite passed: 60 tests with real MongoDB/Redis, including oldest-token replay, simultaneous refresh, absolute expiry, logout/all, late session insertion, changed accounts, signature/claim rejection, CSRF/origin/CORS, role boundaries, atomic rate limiting and failure behavior. Ruff passed. The initial run exposed a cookie UTC formatting bug, now fixed and covered. A bounded integration setup timeout was increased to accommodate local index creation; authentication policy was unchanged.

Browser acceptance is not claimed. The previous frontend context still reads an obsolete CSRF cookie and requires the Step D update to this protocol. Interactive UI and full Admin operations remain incomplete.

## Next step and learning review

Step C: finish Admin-only user creation/listing/update/disable operations, session invalidation for privileged changes, last-active-Admin concurrency protection, audit coverage and negative authorization tests. Do not add uploads or RAG in that step.

The central tradeoff is keeping database session validation alongside JWTs: it adds a lookup but gives current authorization and immediate revocation. Refresh rotation uses compare-and-swap to resolve competing writes, while a token-version epoch protects logout-all against late inserts. CORS restricts browser response access; CSRF controls protect ambient cookie writes. They solve different problems and are both tested here.

Final runtime verification: Docker API rebuild passed with frozen dependencies. The real HTTP smoke passed for login, current user, CSRF restoration, refresh, logout-all, fresh login, logout and revoked access. Main API liveness/readiness returned HTTP 200 and API/MongoDB/Redis/Azurite are healthy. Offline lockfile consistency passed. No frontend files were changed or browser checks claimed.


Cleanup verification: the failed initial concurrent test left one audit-only disposable database after its exception. That exact identified test database was removed after inspection. The successful final suite and HTTP smoke left no Step B test databases, and the application user count remains zero. No commit or push was performed.
