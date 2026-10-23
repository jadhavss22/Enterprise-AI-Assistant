# Implementation Plan

## Approved stack amendment

The approved frontend is Next.js with TypeScript and App Router under `frontend/`. FastAPI remains the only application backend. Next.js API routes will not duplicate authentication, authorization, document processing, or RAG logic.

## Approved implementation constraints

- Initial scaffold only; authentication, uploads, ingestion, RAG, citations, and agentic operations are placeholders.
- Private sharing stays within the uploader's department; Admin access is retained and audited.
- Saved answers are hidden when supporting documents become inaccessible, and source access is rechecked.
- Azure Container Apps is the provisional Next.js deployment target; final validation and paid provisioning are deferred.
- Local development uses the installed `docker compose` command and does not require a Compose downgrade.

## Initial verification target

Verify the frontend starts, FastAPI liveness/readiness endpoints respond, MongoDB/Redis/Azurite are reachable in Compose, and the Celery worker connects to Redis. Any unavailable Docker engine or external service is reported as an unresolved environment issue.

## Scaffold maintenance milestone — complete

The initial scaffold maintenance milestone is accepted as complete. Locked frontend and backend installs, frontend lint/typecheck/build, backend tests/lint, Docker Compose startup, API liveness/readiness, real dependency failure/recovery, Celery round trip, and placeholder route smoke checks were verified.

### Tracked dependency issues

- `GHSA-vfj7-8cjw-p6xm` / `CVE-2026-93687` remains in the development-only path `eslint-config-next -> @next/eslint-plugin-next -> fast-glob -> micromatch -> braces`. Revisit when the official advisory publishes a patched `braces` release, or when the Next.js lint chain removes the vulnerable version. Do not use `npm audit fix --force` as a mitigation.
- ESLint peer-range warnings remain because transitive `eslint-plugin-import`, `eslint-plugin-jsx-a11y`, and `eslint-plugin-react` metadata declares support through ESLint 9 while the project uses current ESLint 10. Revisit when those plugins publish ESLint 10 peer ranges or when `eslint-config-next` changes its dependency chain. The actual ESLint 10 check passes and the packages are development-only.

## Proposed next slice — authentication and authorization (not implemented)

### Auth foundation benchmark and decisions

The local Argon2id benchmark measured approximately 215 ms for time=2 with
64 MiB and parallelism=1, 302 ms for time=3 with 64 MiB and parallelism=1,
and 820 ms for time=3 with 128 MiB and parallelism=1. The initial selected
profile is Argon2id time=3, memory=65536 KiB, parallelism=1, hash length 32,
and salt length 16. The benchmark is hardware-dependent; production capacity
testing remains a deployment prerequisite.

Authentication decisions carried into implementation: access tokens expire in
10 minutes; refresh sessions expire absolutely after one day and rotation does
not extend that deadline; one active department is stored per user; and Admin
creates users by selecting an existing active department. Production audit-log
retention, signing-key storage, and signing-key rotation are deployment
prerequisites and are intentionally not assigned invented retention periods or
automatic deletion behavior here.

### Authentication checkpoint — unfinished work and pending verification

This checkpoint contains authentication work in progress; authentication is
not complete. Remaining work includes:

- Resolve the Windows `npm ci` `ENOTEMPTY` cleanup issue and rerun frontend
  lint, TypeScript checks, and production build after the auth UI changes.
- Rerun Docker Compose, MongoDB/Redis readiness, Celery, and end-to-end auth
  checks when Docker Desktop is reachable.
- Add integration tests for concurrent refresh requests, refresh-token reuse
  after the grace window, disabled accounts, role changes, logout-all, and
  Admin authorization.
- Complete Admin user update, disable, and session-revocation operations.
- Expand privacy-safe audit events to login failures, refresh reuse, logout,
  logout-all, and administrative changes.
- Finish the README authentication setup and verification results after the
  blocked checks are rerun.

Do not treat this checkpoint as production-ready authentication. Uploads,
ingestion, RAG, and agentic operations remain outside the current slice.

This proposal is derived from the approved PRD/SDD requirements for Admin-created password accounts, JWT expiry, Employee/Manager/Admin RBAC, active-session and current-role validation, metadata-only audit events, and basic rate limiting. Uploads, ingestion, RAG, citations, streaming answers, and agentic operations remain outside this slice.

### Proposed security defaults

- First Admin: create through an explicit local bootstrap command that requires `BOOTSTRAP_ADMIN_EMAIL` and a password supplied interactively or through a one-time secret file excluded by Git. Refuse to run if an Admin already exists, never ship a default password, and write only a metadata audit event.
- Passwords: Argon2id using the maintained password-hashing library defaults after capacity review; store only the encoded Argon2id hash. Enforce a minimum length and breached-password review later without logging passwords.
- Access tokens: short-lived JWTs held only in browser memory; include a session identifier, user identifier, role snapshot, issuer, audience, issued-at, and expiry. Every protected backend request rechecks the session, account status, and current role rather than trusting the role claim alone.
- Refresh tokens: opaque random rotating tokens in an HttpOnly, Secure, SameSite=Lax cookie; store only a hash and token-family metadata in MongoDB. Rotation invalidates the prior token. Reuse detection revokes the token family and records a privacy-safe security event.
- Logout: revoke the current session and refresh token. Logout-all revokes every active session for the user and invalidates all refresh-token families.
- CSRF: require a same-origin CSRF token header for cookie-authenticated state-changing requests, validate Origin/Referer where available, and keep refresh/logout endpoints protected by the same policy. Bearer access-token API calls remain explicitly authorized by the backend.
- Local versus production cookies: local HTTP uses `Secure=false` only for the development profile and `SameSite=Lax`; production HTTPS uses `Secure=true`, a narrowly scoped cookie path, and no broad domain unless deployment requires it.
- Rate limits: Redis-backed limits for login, refresh, bootstrap, password creation/reset, and logout-all. Return generic login failures and never reveal whether an account exists.
- Audit events: record actor/session/user IDs, event type, result, request correlation ID, timestamp, and coarse source metadata. Do not record passwords, tokens, prompts, answers, or document text.

### Proposed files and dependencies

- Backend modules under `backend/app/auth/`: password hashing, JWT/access-token service, refresh-session repository, CSRF checks, rate limits, dependencies, and auth routes.
- MongoDB repositories/models under `backend/app/infrastructure/` for users, sessions, refresh-token families, and audit events.
- `backend/tests/test_auth.py`, `test_sessions.py`, `test_csrf.py`, and `test_authorization.py` for unit, API, and negative security tests.
- Frontend `frontend/src/lib/auth/` for in-memory access-token state, authenticated fetch, refresh coordination, logout, and session-expiry handling. No Next.js API routes.
- Frontend placeholder navigation components updated to render links by the current role, while backend authorization remains authoritative.
- Add a maintained Argon2id password-hashing dependency, JWT/JWS library, and any narrowly scoped CSRF/rate-limit helpers only after compatibility review. Redis and MongoDB are already approved runtime dependencies.

### Proposed API contracts

- `POST /api/v1/auth/bootstrap-admin` — local-only, one-time bootstrap; returns a generic success response and never returns credentials.
- `POST /api/v1/auth/login` — accepts email/password; sets the refresh cookie and returns a short-lived access token plus safe user/session metadata.
- `POST /api/v1/auth/refresh` — rotates the refresh cookie and returns a new in-memory access token; reuse returns a generic unauthorized response and revokes the family.
- `POST /api/v1/auth/logout` — revokes the current session and clears the refresh cookie.
- `POST /api/v1/auth/logout-all` — revokes all sessions for the current user.
- `GET /api/v1/auth/me` — validates access token, active session, account status, and current role; returns current user and permissions.
- `POST /api/v1/admin/users` — Admin-only creation of an Employee, Manager, or Admin account; password is accepted only over TLS/approved local development transport and is never returned or logged.
- `PATCH /api/v1/admin/users/{user_id}` — Admin-only status, role, and department changes; prevents unsafe removal of the last active Admin.
- `GET /api/v1/admin/sessions` and `DELETE /api/v1/admin/sessions/{session_id}` — Admin session visibility/revocation with privacy-safe audit events.

### Acceptance tests

- Bootstrap succeeds once, refuses a second run, requires explicit non-default input, and creates no committed secret.
- Passwords are Argon2id hashes; plaintext passwords and tokens never appear in responses, logs, audit events, or database records.
- Valid login, invalid login, disabled-account login, expired access token, expired refresh token, and malformed-token cases behave generically and safely.
- Refresh rotation invalidates the old token; reuse detection revokes the family; logout and logout-all revoke the expected sessions.
- Every protected request rejects an inactive session, disabled account, stale/changed role, wrong issuer/audience, and expired token.
- CSRF and Origin/Referer checks reject cross-site state-changing requests while same-origin requests succeed.
- Login, refresh, bootstrap, and logout-all limits trigger with generic responses and recover after the window.
- Employee, Manager, and Admin permissions are enforced in the backend; frontend role-aware navigation never grants authority by itself.
- The last active Admin cannot be disabled or demoted without a safe replacement.
- Audit events contain metadata only and capture successful and rejected security actions.
- Local HTTP cookie behavior works only in the development profile; production configuration requires HTTPS and Secure cookies.

