# Authentication and authorization implementation plan

Reviewed 24 October 2026. Planning only; no application code changed.

## Milestone and scope

Accept scaffold maintenance as complete based on the previously reported verification, not a fresh execution of those checks. Authentication code already exists as unfinished work. Complete and verify that work rather than restarting it. The PRD and SDD Markdown companions summarize the specifications; the preserved Word originals contain the full schema and API contracts previously reviewed. Earlier contradictory proposals in implementation-plan.md are superseded by this dated plan.

Uploads, ingestion, document ACL implementation, RAG, citations, answer streaming, agentic operations, password reset, and a general session-administration dashboard are outside this slice.

## Tracked maintenance issues

- DEP-01: Previously reported GHSA-vfj7-8cjw-p6xm / CVE-2026-93687 in the development lint chain through braces. Record as outstanding, not fixed. Recheck the advisory during the next dependency update and before production; resolve when a patched compatible dependency or replacement chain is available. Reassess immediately if the package becomes runtime-reachable or processes untrusted patterns. No forced audit fixes or TLS bypasses. Current advisory status was not re-fetched in this planning pass.
- DEP-02: Previously reported ESLint 10 peer-range warnings in transitive plugins. Revisit when plugin peer metadata/configuration changes, on dependency updates, or immediately if lint or installation fails. Retain reproducible installs and test the actual supported combination; passing lint does not establish full upstream compatibility.

## Existing gaps to correct

- Replace the obsolete HTTP bootstrap proposal with the existing CLI bootstrap approach. No public bootstrap endpoint.
- Align GET /api/v1/auth/me with the SDD's GET /api/v1/users/me.
- Reconcile UUID user references, lowercase role values, and sessions/audit_events collection names with the SDD's ObjectId references, uppercase enums, auth_sessions, and audit_logs. Propose a safe local migration if data exists; do not silently discard accounts.
- Current refresh lookup retains only the immediately previous token. Retain consumed-token fingerprints through the family's absolute expiry so older replay can identify and revoke the family.
- Current CSRF comparison lacks Origin validation and its cookie path prevents ordinary frontend pages from reading it. Define a browser-usable session-bound CSRF contract and matching CORS headers.
- Current Redis INCR then EXPIRE is not atomic. Use an atomic bounded limiter; never include raw refresh-token fragments in Redis keys.
- Complete Admin listing/update, revocation behavior, audit coverage, and real concurrent integration tests.

## Security and data contracts

1. Bootstrap: explicit CLI, interactively entered password, no defaults or secret arguments in shell history. Atomic one-time claim plus recoverable failure behavior prevents concurrent first-Admin creation. Seed active departments idempotently. Audit successful/failed privileged changes without credentials. No network bootstrap endpoint.
2. Users: normalized unique email, Argon2id encoded hash, current role/status, one active department reference, token_version. Admin chooses an existing active department. Enforce role and department on the server. Protect the last active Admin against concurrent disable/demotion, not just sequential checks.
3. Passwords: retain the recorded benchmark starting profile (time=3, memory=65536 KiB, parallelism=1); rebenchmark for deployment hardware. Proposed policy: 15-128 characters, permit spaces/paste/password managers, no arbitrary composition rules or silent truncation. Generic login failures and dummy hash verification for unknown users reduce enumeration. Password-policy values remain a proposal.
4. JWT: ten-minute lifetime, explicitly allowed signature algorithm, issuer/audience/sub/sid/token_version/iat/exp validation. Memory-only browser storage. Every protected request reads the active unexpired session and current account/status/role; token role snapshots never grant privileges. Production must reject development keys and unsafe configuration. Rotation/key storage in Azure Key Vault remain deployment prerequisites, with old-key verification bounded by token lifetime.
5. Refresh: random opaque tokens, only hashes persisted, absolute one-day session expiry unchanged by rotation. Cookie lifetime follows remaining session lifetime. Atomic compare-and-swap rotates once; retain consumed fingerprints and family identity. Replayed consumed tokens revoke the family. Random unknown tokens must not revoke unrelated sessions. Default proposal is strict replay revocation, with frontend single-flight and cross-tab coordination; do not silently preserve the current two-second grace exception. Any grace alternative needs an explicit security tradeoff and tests.
6. Logout: revoke session server-side and clear cookies with matching attributes. Logout-all increments user token_version and revokes sessions, including race-safe handling of concurrent login/refresh. Disable and privilege changes invalidate affected sessions. Define a request that has already passed authorization as in-flight; do not promise cancellation of all concurrent work.
7. CSRF/browser: refresh token is HttpOnly. Return a session-bound CSRF token through an allowed-origin endpoint for page reload restoration; keep it in memory and require X-CSRF-Token on cookie-authenticated writes. Validate Origin against exact frontend origins, using validated Referer only as a deliberate fallback; reject missing provenance on browser cookie writes. Protect login from login CSRF via origin validation. Cross-origin local frontend/API calls use credentials: include, explicit credentialed CORS and CSRF header allowlisting. No wildcard origins. Never put tokens in URLs or localStorage.
8. Cookies: local HTTP may use Secure=false only under explicit development configuration; production requires Secure=true and HTTPS. Refresh cookie is host-only, HttpOnly, path /api/v1/auth. SameSite=Lax assumes same-site deployment. Validate frontend/API domain topology before deployment; cross-site cookies require a separately reviewed SameSite=None configuration. No authentication logic in Next.js routes.
9. Rate limiting: proposed configurable defaults are login 5 attempts/account/15 minutes plus 30/IP/15 minutes; refresh 30/session/minute plus an IP bound for invalid cookies; logout-all 5/user/minute; Admin writes 30/Admin/minute. Use hashed account identifiers and verified session identities, bounded TTL keys, atomic Redis operations, 429 with Retry-After. Trust forwarded IPs only from configured proxies. Redis failure yields safe 503 for protected auth mutations. Tune through tests and deployment load measurements.
10. Audit: login success/failure, refresh reuse, logout/all, bootstrap, user creation/update/disable, authorization denials and privileged reads. Store event, actor/resource IDs, outcome, UTC time, request ID and bounded sanitized metadata. Never log credentials, tokens, hashes, full request bodies, or sensitive content. Restrict writes/reads; define audit failure behavior for security mutations explicitly and test it. Retention and tamper-resistance configuration are production prerequisites; no invented retention days or automatic purge.

## Files and dependencies

Extend existing backend/app/auth/{models,schemas,storage,security,tokens,dependencies,service,routes,admin_routes,bootstrap_admin}.py. Add focused csrf.py, rate_limits.py and audit.py helpers if needed. Update backend/app/config.py and main.py for settings, CORS and routes. Keep schema/index initialization and any migration explicit and idempotent.

Extend frontend/src/lib/auth/ and the existing auth/session context; add authenticated fetch, single-flight/cross-tab refresh coordination, memory-only token handling, loading/expired states and safe logout cleanup. Update frontend/app/login/page.tsx, admin/users/page.tsx and shared navigation. Backend checks remain authoritative even if users manually navigate to hidden routes.

Tests: backend/tests/test_auth.py, test_sessions.py, test_csrf.py, test_authorization.py, test_admin_users.py and real MongoDB/Redis integration tests. Add frontend session/refresh tests with the chosen existing test setup; propose any new test dependency before installation. Update README.md, .env.example, implementation-plan.md and backend/uv.lock only as required by implementation.

Already declared runtime libraries: argon2-cffi, PyJWT, Pydantic/settings, MongoDB driver, redis, FastAPI. Test dependencies: pytest, pytest-asyncio and HTTPX (prefer development-only if unused by application code). Do not add a second auth framework or Next.js backend. Verify installed driver/library compatibility before edits; resolve versions with uv.lock/package-lock.json.

## API contracts

| Method/path | Request and authorization | Success |
|---|---|---|
| CLI bootstrap | Explicit operator email/name/department/password | Admin created; no secrets echoed |
| POST /api/v1/auth/login | email/password; allowed browser origin and rate limits | 200 access_token, token_type, expires_in, safe user; refresh cookie |
| GET /api/v1/auth/csrf | Active refresh session; allowed origin, credentialed CORS; Cache-Control: no-store | 200 session-bound CSRF token; never rotates refresh |
| POST /api/v1/auth/refresh | Refresh cookie, CSRF header and origin validation | 200 access token/user; rotated cookie |
| POST /api/v1/auth/logout | Cookie session, CSRF and origin validation | 204 revoked/cleared, idempotent |
| POST /api/v1/auth/logout-all | Bearer current user plus CSRF/origin for cookie write | 204 all user sessions invalidated/cookies cleared |
| GET /api/v1/users/me | Bearer, active session, current user | 200 safe current user/role/department |
| POST /api/v1/admin/users | Current Admin; email/name/password/role/department_id | 201 safe user; duplicate 409 |
| GET /api/v1/admin/users | Current Admin; bounded cursor pagination | 200 users and next_cursor |
| PATCH /api/v1/admin/users/{id} | Current Admin; status/role/department | 200 updated safe user; invalidate affected sessions |

CSRF endpoint is a proposed supporting contract. No new Admin session browsing or password-reset endpoints in this slice. Use the SDD error envelope with code/message/request_id; 401 invalid authentication, 403 permission/CSRF, 409 conflict, 422 validation, 429 rate limiting, 503 required dependency unavailable. All auth responses are non-cacheable.

## Acceptance and implementation sequence

A. Models/indexes/bootstrap: unique email enforced under concurrent creation; valid department required; hashes only in DB; simultaneous bootstrap creates at most one first Admin; interrupted bootstrap can recover safely.
B. Session APIs: correct/incorrect/disabled login; malformed/signature/issuer/audience/expiry/token-version rejection; rotation never extends deadline; replay of tokens older than one rotation revokes family; simultaneous refresh has one winner and documented safe replay behavior; logout/all and role/disable changes invalidate existing tokens. Test races against real MongoDB, not only mocks.
C. Authorization/admin: Employee and Manager receive 403 on Admin APIs; direct navigation cannot bypass checks; user input cannot self-promote; concurrent changes cannot remove the last active Admin; Admin reads and mutations produce sanitized audit records.
D. Browser: login, reload restoration, expired-token refresh, multi-tab refresh/logout, disabled-user handling, role-aware navigation, accessible errors. No token persists in browser storage. Foreign/missing origins and invalid CSRF fail; valid local cross-origin credentials work; cookie attributes and production configuration are verified.
E. Reliability/verification: atomic rate-limit expiry, bounded concurrency, dependency failure behavior, privacy-safe audits, no secrets in logs, backend tests/lint, frontend lint/typecheck/build, Compose readiness and auth end-to-end tests. Keep builds separate from running dev .next output. Update README with actual results, not assumed passes.

Before coding, explain JWT plus database-session tradeoffs, refresh races, CSRF versus CORS, and role enforcement. After each step, review tests and ask a few interview questions. Final readiness depends on completed acceptance tests; do not label authentication complete based on scaffolding alone.
