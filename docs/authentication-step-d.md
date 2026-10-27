# Step D — Frontend authentication integration

## Scope

The Next.js App Router client now integrates with the FastAPI authentication and Admin-user APIs. It keeps access tokens and CSRF tokens in JavaScript memory, uses the backend's HttpOnly refresh cookie, and coordinates cookie-changing operations across tabs with the Web Locks API. BroadcastChannel publishes only a generic session-changed signal; it never carries tokens or user data. API authorization remains enforced by FastAPI.

The frontend now provides guarded signed-in routes, role-aware navigation, session restoration/revalidation, logout and logout-all, profile/security settings, and Admin user create/list/filter/update flows with department selection. The login form displays a clear service-unavailable state and supports safe local return paths. All other product pages remain explicitly marked as planned placeholders. Uploads, ingestion, RAG, answer streaming, analytics, password reset, and audit browsing are outside Step D.

## Browser/API behavior

- Session restoration gets a CSRF token from `GET /api/v1/auth/csrf`, then calls `POST /api/v1/auth/refresh` with that token and browser credentials.
- Login, refresh, logout, and logout-all share one exclusive browser lock to prevent concurrent refresh-token rotation races between tabs.
- Successful access tokens are held in component-provider memory only. No token is placed in local storage, session storage, cookies readable by JavaScript, broadcast messages, or URLs.
- Authenticated API requests send a bearer access token. A single 401-triggered refresh/retry is allowed for the same account. 403, 409, 429, and 503 are surfaced without blind retries.
- Tabs broadcast only `session-changed`; listeners re-check the HttpOnly cookie with the backend. Visible authenticated tabs periodically call `/api/v1/users/me` to reflect disabled accounts and changed roles.
- Routes that require a role are hidden from navigation and show an access-denied page on direct navigation. These client checks are presentation only; backend authorization remains authoritative.
- Admin user management uses API roles/statuses in uppercase, page size 20, the API cursor, and active departments from the read-only choices API. Last-active-Admin and duplicate-email conflicts receive actionable messages. Mutations are not automatically retried.

## Local use

Start Docker Desktop, then run `docker compose up -d --build` from the repository root. The frontend is available at `http://localhost:3000`, and the API at `http://localhost:8000`. Create the first Admin through the documented interactive backend bootstrap command; do not add credentials to `.env.example`, source code, shell history, or Git. Local HTTP sets the development refresh cookie without `Secure`; hosted configurations require HTTPS and a Secure cookie.

## Verification record

27 October 2026: frontend lint, TypeScript check and Next.js production build pass. A local Next.js development server serves the rendered login screen. HTTP smoke checks returned 200 for `/`, `/login`, `/home`, `/chat`, `/documents`, `/history`, `/settings`, `/admin`, `/admin/users`, and `/manager/upload`; API liveness/readiness returned 200 with MongoDB, Redis and Azurite ready. A signed-out direct visit to `/admin/users` redirected to `/login?next=%2Fadmin%2Fusers`. `GET /api/v1/auth/csrf` returned the expected 401 without an existing refresh cookie and included credentialed CORS headers for `http://localhost:3000`. No real Admin credentials or test accounts were created. Therefore successful sign-in, refresh rotation, logout-all, and Admin write actions still require a user-created local Admin and remain to be verified interactively.
