# Enterprise AI Assistant

This repository contains the runnable foundation and backend authentication APIs for the Enterprise AI Assistant.

Account foundation (Step A), backend session APIs (Step B), Admin management/authorization (Step C), and frontend auth integration (Step D) are implemented. Admin user management is available to Admin accounts. Chat, document uploads, ingestion, RAG, citations and agentic operations remain planned placeholders.

## Prerequisites

- Docker Desktop running.
- Docker Compose available as `docker compose`. The installed Compose CLI was verified as `5.6.1`.
- Node.js 22 is required for the frontend toolchain; Docker runs the frontend with Node 22. Next.js 16 requires Node.js `>=20.9`, so Node 22 is compatible.
- `uv` for locked backend development commands. The repository uses Python 3.12 for the backend.

## Configuration

Copy `.env.example` to `.env` before starting the stack:

```powershell
Copy-Item .env.example .env
```

The local values are development-only. Never commit `.env` or real credentials.

## Start with Docker Compose

```powershell
docker compose up -d --build
```

Open `http://localhost:3000` for the frontend and `http://localhost:8000/docs` for the FastAPI documentation.

Compose builds the API and worker from `backend/Dockerfile`. The image installs the backend from `backend/uv.lock` with `uv sync --frozen`; it does not run an unconstrained `pip install` at container startup.

## Local backend checks

From `backend/`:

```powershell
..\.venv\Scripts\uv.exe sync --frozen --extra dev
..\.venv\Scripts\uv.exe run --frozen pytest
..\.venv\Scripts\uv.exe run --frozen ruff check app tests
```

`pytest` currently covers liveness and the required-dependency failure response. The tests use async HTTPX ASGI transport, avoiding the deprecated Starlette synchronous TestClient integration. The health endpoint probes MongoDB, Redis, and Azurite concurrently with bounded timeouts; it returns HTTP 503 until all required dependencies are reachable.

## Frontend checks

From `frontend/`:

```powershell
npm ci
npm run lint
npm run typecheck
npm run build
```

The frontend sign-in, session management, role-aware route shell and Admin user-management forms are connected to FastAPI. Chat, documents, history, manager uploads, analytics and audit browsing remain clearly labeled future placeholders. Frontend route checks do not replace backend authorization.

The frontend currently uses Next.js `16.4.2`, React `19.3.1`, ESLint `10.13.0`,
and `eslint-config-next` `16.4.2`. Next 16 uses native flat ESLint config,
removes `next lint`, and no longer runs lint automatically during `next build`,
so lint remains an explicit `npm run lint` check.

## Verify

```powershell
Invoke-WebRequest http://localhost:8000/health/live
Invoke-WebRequest http://localhost:8000/health/ready
docker compose ps
docker compose logs worker
```

After the stack is running, verify an actual Celery round trip:

```powershell
docker compose exec worker python -c "from app.workers.celery_app import scaffold_ping; result = scaffold_ping.delay(); print(result.get(timeout=20))"
```

Expected task output is `worker-ready`.

To verify the required-dependency failure behavior, stop Redis, query readiness,
then start Redis again:

```powershell
docker compose stop redis
curl.exe --include http://localhost:8000/health/ready
docker compose start redis
docker compose ps redis
curl.exe --include http://localhost:8000/health/ready
```

The first readiness response is HTTP 503 with Redis unavailable; after Redis is
healthy again, readiness returns HTTP 200.

## Stop

```powershell
docker compose down
```

Use `docker compose down -v` only when you intentionally want to remove local MongoDB, Azurite, and dependency volumes.

## Verification status for this scaffold update

- Docker engine: passed (`docker info` reported Docker Engine `29.9.2`).
- Compose CLI: passed (`docker compose version --short` reported `5.6.1`).
- Compose configuration: passed with `docker compose config --quiet`.
- Compose startup: passed with MongoDB, Redis, Azurite, API, worker, and frontend running; MongoDB, Redis, Azurite, and API reported healthy.
- API liveness: passed with HTTP 200 and `{"status":"ok"}`.
- API readiness: passed with HTTP 200 and MongoDB, Redis, and Azurite all reported ready.
- Readiness failure/recovery: passed against the real stack. With Redis stopped, readiness returned HTTP 503 and Redis unavailable; after Redis restarted and became healthy, readiness returned HTTP 200.
- Celery round trip: passed through Redis and the worker; `scaffold_ping` returned `worker-ready`.
- Backend locked install: passed with `uv sync --frozen --extra dev` and `backend/uv.lock`.
- Backend tests: passed, 3 tests, with no Starlette/httpx deprecation warning.
- Backend Ruff checks: passed.
- Frontend locked install: passed with `npm ci` using `frontend/package-lock.json`. The first clean install took about three minutes because many registry tarballs were slow cache misses; npm logs showed downloads taking roughly 30â€“136 seconds. TLS remained enabled and the lockfile was preserved.
- Frontend lint: passed with `npm run lint` using Next 16 native flat config.
- Frontend TypeScript: passed with `npm run typecheck`.
- Frontend production build: passed with `npm run build` in an isolated temporary container, separate from the running Compose dev server.
- Placeholder routes: HTTP smoke tests passed for `/`, `/home`, `/chat`, `/documents`, `/history`, `/settings`, `/login`, `/manager/upload`, `/admin`, and `/admin/users`. Interactive browser click-through was not run because no browser surface was connected in this session.
- UI screenshots: 25 PNGs extracted to `docs/assets/ui-screenshots/` and linked from `docs/UI_reference.md`; the original Word file is preserved.
- Environment safety: `.env` exists locally, is ignored by `.gitignore`, and no credential-named files are tracked.

The earlier audit findings were addressed in the dependency-maintenance update.
The final audit details and remaining development-only finding are documented
below. Production builds are run in an isolated temporary container so they do
not share `.next` artifacts with the running dev server.

## Final dependency-maintenance results

The final frontend versions are Next.js `16.4.2`, React `19.3.1`, React DOM
`19.3.1`, ESLint `10.13.0`, `eslint-config-next` `16.4.2`, TypeScript `5.9.3`,
and matching React and Node type packages. Node 22 is compatible with Next 16's
minimum Node requirement of `>=20.9`.

The production audit is clean: `npm audit --omit=dev` reports zero findings.
The full audit retains five high development-only findings through this path:

```text
eslint-config-next
â””â”€â”€ @next/eslint-plugin-next
    â””â”€â”€ fast-glob
        â””â”€â”€ micromatch
            â””â”€â”€ braces
```

All five findings refer to `GHSA-vfj7-8cjw-p6xm` / `CVE-2026-93687`, affecting
`braces <=3.0.3`. The official advisory lists no patched release. These packages
are lint/build tooling from `devDependencies`; they are not bundled into the
Next.js runtime and are omitted by `npm ci --omit=dev`. Mitigation is to keep
lint tooling isolated from production, run lint in CI, avoid attacker-controlled
patterns as lint input, and monitor upstream releases. No force audit fix or
security bypass was used.

Next 16 required the native flat ESLint imports and generated TypeScript
settings (`jsx: react-jsx` and `.next/dev/types`). The approved architecture
and application behavior are unchanged. `npm ci` still emits peer-range
warnings because some transitive lint plugins declare support through ESLint 9,
but the actual ESLint 10 run passes; this is development-tool metadata rather
than a production audit finding.

## Next operator check

To verify signed-in flows, start the local stack and create your own development Admin through the interactive bootstrap command in [Step A](docs/authentication-step-a.md). Then sign in at `http://localhost:3000`, verify `/admin/users`, and try creating an Employee account. Do not send your password to chat or place it in a command or tracked file. Authenticated refresh/logout and Admin-write browser checks have not been run because automated verification did not create credentials.



## Authentication Step A - completed 26 October 2026

Persistence models, indexes, three idempotent department seeds, and safe CLI-only
first-Admin bootstrap are implemented. See [Step A setup and verification](docs/authentication-step-a.md)
for commands, data preservation rules, model/API serialization and tests.

The backend suite passes with 22 tests including real MongoDB, and Ruff passes.
This establishes the account foundation; complete login/refresh security, CSRF,
Admin operations and browser verification in Steps B-D before treating
authentication as complete. No real Admin credentials were created by the agent.


## Authentication Step B - completed 27 October 2026

Backend login, session-bound CSRF restoration, strict refresh rotation/replay
protection, logout/all and GET /api/v1/users/me are implemented. The full backend
suite passes with 60 real MongoDB/Redis-backed tests, and Ruff passes.
See [Step B protocol, verification and limitations](docs/authentication-step-b.md).
The frontend still needs Step D's CSRF and refresh coordination updates; this is
not yet a completed browser authentication experience. No packages were added.


## Authentication Step C - completed 27 October 2026

Admin user creation, cursor listing, updates/disabling, session invalidation and
concurrent last-active-Admin protection are complete. The backend suite passes
96 tests and the real HTTP Admin/authentication smoke passes. See
[Step C contracts, local setup and interview notes](docs/authentication-step-c.md).

Local MongoDB now uses a one-node replica set for transactions. Start it through
`docker compose up -d --build api`; the one-shot `mongo-init` service exiting 0
means initialization succeeded. Existing data volumes are preserved. Host-side
tests/Compass use `mongodb://localhost:27017/?directConnection=true`.

The next step is Step D frontend integration. No real accounts were created by
the tests, and no dependency changes were needed.


## Authentication Step D

The frontend is served at `http://localhost:3000`. It uses the FastAPI `/api/v1/auth` and `/api/v1/admin` endpoints. For browser use, keep the API `FRONTEND_ORIGIN` set to `http://localhost:3000`; do not expose tokens in browser storage. To sign in locally, first start the stack, then create your real development Admin interactively as described in [Step A bootstrap instructions](docs/authentication-step-a.md). The CLI asks you for the password in the terminal; do not put it in a command, chat, or tracked file.

From `frontend/`, run `npm run dev` when using the local Node installation. If you use the Compose frontend instead, stop the local dev server first because both use port 3000.

Lint, typecheck and production build pass. HTTP checks confirmed all routes respond and signed-out Admin navigation returns to login. No Admin/test account was created by automated verification, so the authenticated browser lifecycle and Admin create/update interactions should be completed after you run the bootstrap command. See [Step D details](docs/authentication-step-d.md).
