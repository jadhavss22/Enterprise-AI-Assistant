## Retrieval commit checkpoint — 1 November 2026

User authorized committing all pending retrieval files and pushing this checkpoint to dev. This is an implementation/review checkpoint, not a declaration that the five open review findings or recovery E2 have been resolved. Preserve startup warm-up, cache-only loading, model readiness, clearer retry messaging, provider-work lifetime bounds and active-version chunk-count consistency as next work. Complete the review fixes and end-to-end recovery verification before proceeding to query rewrite/answer generation.

Included: permission-aware search backend/UI, regression tests, synthetic evaluation fixtures/results, operational limitations, review report and tracker. Prior verified baseline is 8716964; use git log and git branch -vv for this checkpoint's generated commit ID and remote synchronization. This entry is written before the commit/push, so it does not claim a successful push in advance.

Latest fresh review evidence: five retrieval tests passed in 22.19 seconds with isolated MongoDB/Redis and provider doubles; backend Ruff, frontend ESLint/TypeScript and diff checks passed. Earlier full-suite/build/live-calibration evidence remains dated separately. Basic candidate-file credential scan found no matching secret patterns; ignored .env remains excluded. Application code was not modified by the review or this checkpoint operation.

## Current retrieval review checkpoint — 1 November 2026

The uncommitted permission-aware retrieval slice was reviewed against baseline 8716964. Five open findings: actual provider work can outlive/reuse concurrency permits; pending reprocessing chunk counts can suppress old active-version passages; user queries still trigger cold model loads; retrieval/model readiness is separate from and absent in current core health checks; and retry UI discards structured codes, Retry-After and safe request IDs. Startup warm-up, cache-only loading, model readiness and clearer retry messaging remain explicit work items.

Fresh verification: five retrieval tests passed in 22.19 seconds using isolated real MongoDB/Redis authentication and provider doubles; full backend Ruff, frontend ESLint/TypeScript and diff checks passed. Two focused provider-double probes reproduced the outstanding-work and active-version count problems. No application code, live providers, private configuration, commits or pushes were changed. No full-suite/production-build/browser rerun is claimed in this review.

E2 recovery is still open: the observed later PineconeConnectionError is independent of cold model load and needs a successful authorized response after connectivity restoration. See [retrieval review](docs/review-retrieval-2026-11-01.md) for priorities, exact code locations, test limits and next steps. Do not treat the prior implementation/calibration success as evidence that these operational issues are resolved.

## Current retrieval checkpoint — 31 October 2026

Approved permission-aware semantic retrieval is implemented on dev and remains UNCOMMITTED. Added backend/app/retrieval (typed POST /api/v1/retrieval/search, current session/ACL/deletion/active-version revalidation, stable chunk identity validation, bounded namespace search, short-lived worker query embeddings, safe failures, rate/request limits), a Documents search panel, real-auth provider-double regressions, an opt-in live evaluation runner, and versioned synthetic calibration data/results. No new dependencies, model subscriptions, answer generation, chat history, query rewrite, streaming, hybrid search or reranking were added.

Fresh checks: 118 backend tests passed in 347.37 seconds; Ruff, frontend full ESLint, TypeScript, uv lock check (126 packages) and diff checks passed. Isolated production build and headless rendered search UI regression passed. The first build was sandbox-blocked (spawn EPERM); its approved rerun passed. Live Celery/CPU MiniLM/Pinecone search through the ASGI endpoint with disposable real authentication returned HTTP 200 for all 8 synthetic cases. Cutoff 0.3 missed 1/6 relevant questions; provisional cutoff 0.25 recovered all 6 and retained empty results on both unrelated questions. These are source-document Recall@5 calibration results on a tiny reused dataset, not production/holdout quality guarantees. No uploaded documents, original ACLs or Pinecone vectors were changed by evaluation.

API, worker and scheduler were restarted and running; API/MongoDB/Redis/Azurite healthy. User Chrome acceptance for the NEW search panel is pending. Source download remains backend-authorized. The single worker can be busy/cold; explicit retry errors are expected. Model changes require reindexing even if dimensions match. Scope is limited to 25 authorized active documents by default; larger-scale retrieval/index layout and load targets require measurement. Review docs/retrieval-plan.md for the contract, limits and exact verification boundaries.

Previous upload checkpoint 8716964 was independently verified on origin/dev via git ls-remote today. The older authentication-failed push note below is historical and resolved for that checkpoint. New retrieval work has NOT been committed or pushed; main is unchanged. Next: user tests the new search panel, then review/commit this slice when authorized, before proceeding to query rewrite and grounded answer generation.

## Current checkpoint — 31 October 2026

Document upload/ingestion milestone accepted for the development checkpoint. User confirmed the final automatic status-update browser check passed. Earlier manual TXT/PDF/DOCX, validation, duplicate, download, department and private-sharing checks are recorded below.

All five findings in the 30 October review now have implemented fixes. Fresh full backend regression: **113 passed in 293.71 seconds**, with real isolated MongoDB/Redis test services and the synthetic Azurite transport test enabled. Storage, broker and vector calls in focused permission/concurrency tests remain test doubles where explicitly described; no new live provider ingestion was run today. Backend Ruff, frontend full ESLint, TypeScript, uv lock check (126 packages), and diff checks passed. An initial test setup attempt failed while local services were stopped; after restoring healthy services the full rerun passed. No new production build or automated pagination browser run is claimed for this checkpoint.

Credential scan found no real private-key/API-key/password matches in candidate source files. Private .env, environments, node_modules and generated output remain ignored. Optional Azure OpenAI, OCR, production load/deployment hardening and permission-aware semantic retrieval remain outside this milestone. The earlier review and pending notes below are historical; this checkpoint supersedes their resolved items.

Branch: dev. Preceding verified commit: 2a8369e. Feature checkpoint committed locally as 40449f8 (feat: complete document upload and ingestion with access controls). Push to origin/dev was attempted but GitHub rejected the stored credentials (Invalid username or token). No remote backup of dev is claimed. Reauthenticate GitHub through VS Code/Git Credential Manager, then run git push -u origin dev and verify the remote hash. No changes to main are authorized. Next feature: permission-aware document retrieval, including current ACL checks before returning chunk content and use of only active index versions.

# Project status and recovery tracker

Last updated: 30 October 2026 (Asia/Calcutta).

This is the current handoff for resuming development without chat history. Update it at every completed feature and whenever work stops with material unfinished changes. Specifications remain authoritative for product behavior; this file records actual delivery and verification.

## Resume here

1. Read this file, `AGENTS.md`, `README.md`, and the instructions applicable to the files being changed.
2. Run `git status --short`, `git branch --show-current`, and `git log -5 --oneline`. Reconcile any newer work before editing.
3. Read `docs/PRD.md`, `docs/System_Design.md`, and the relevant feature notes. The preserved Word originals contain the full schema/API contracts.
4. Resume from the next feature below. Preserve unrelated local changes. Do not assume local services, credentials, accounts, or previous test results still exist.

## Repository and checkpoint

- Repository: Enterprise-AI-Assistant; remote `origin` is the project's GitHub repository.
- Current working branch: `dev`. All onward development and commits belong on `dev` unless the user explicitly requests another branch.
- Latest completed feature commit: `2a8369e` — `feat: complete authentication and admin user management`.
- Previous scaffold commit: `7475d25` — `chore: checkpoint scaffold and authentication work in progress`.
- `main` remains at the scaffold checkpoint. Do not commit, merge, or push changes to `main` without explicit instruction.
- No push of the feature commit was performed in this chat. Confirm remote state before assuming GitHub contains it.
- This tracker and its workflow instructions are new local documentation, pending commit. A newline-only README change was present before this tracker was added.
- Current checkpoint: upload/ingestion is implemented but its 30 October review identified open correctness/security fixes; see the review record below.

Local path used in this session: `C:/Users/acer/Desktop/Enterprise-AI-Assistant`. A separate `Documents/ChatGPT/Enterprise-AI-Assistant` folder contains helper scripts; it is not the application checkout. On a replacement laptop, clone the GitHub repository and use the new checkout path.

## Feature delivery

| Feature | Actual status | Evidence / notes |
| --- | --- | --- |
| Scaffold and local infrastructure | Implemented; earlier verification recorded | Next.js/TypeScript, FastAPI, MongoDB, Redis, Celery, Azurite; see README |
| A: Accounts, schema/indexes, CLI bootstrap | Implemented and backend-tested | `docs/authentication-step-a.md` |
| B: Login, JWT/session enforcement, rotating refresh, logout/all, CSRF, rate limits, audit | Implemented and backend-tested | `docs/authentication-step-b.md` |
| C: Admin user management and authorization | Implemented and backend-tested | `docs/authentication-step-c.md`; Employee/Manager denial and concurrent last-Admin protection covered |
| D: Frontend authentication and Admin integration | Implemented; selected signed-in browser acceptance passed | `docs/authentication-step-d.md`; latest verification below supersedes its older pending-browser note |
| Document upload, ACLs, source access and ingestion | Implemented and reviewed; fixes required before completion | Backend documents/ingestion modules, Documents and Manager Upload pages, Admin ingestion queue/retry; see feature record below |
| RAG, citations, streaming answers, chats and feedback | Not implemented | Existing product pages are placeholders |
| Dashboards, production hardening and Azure deployment | Deferred | Follow approved phase boundaries |
| Agentic operations | Deferred to Phase 4 | No autonomous operational actions |

## Latest verification and corrections

27 October 2026, against the Desktop application checkout:

- Backend suite: **96 passed** using real local MongoDB and Redis, including negative Admin authorization checks. This full-suite run preceded the small follow-up fixes below.
- Backend Ruff, frontend ESLint, TypeScript (`tsc --noEmit --incremental false`), and tracked diff checks passed during review. Staged checks subsequently found two documentation EOF blank lines, which were removed before the feature commit.
- Follow-up fixes included in `2a8369e`: share/await the refresh promise without an orphan rejected promise; expose `Retry-After` and `X-Request-ID` through CORS; use one destination-aware login redirect; avoid forcing JSON content type on FormData; catch the gate's refresh-retry rejection; synchronize the revalidated user reference.
- A real headless Edge acceptance run used two tabs, an isolated API, a disposable MongoDB database/Redis namespace, and ten-second access JWTs. It passed requested-page login, simultaneous tab restoration/rotation, expired access-JWT refresh and API retry, logout propagation, refresh outage/retry with no unhandled browser errors, and logout-all propagation. Local/session storage were empty in the tested browser context. Disposable test data and the temporary API were cleaned up.
- Browser harness files were created outside this repository as `browser_auth_check.py` and `browser_auth_check.cjs` in the separate Documents folder. They are **not committed tests** and are not recoverable from the application Git repository. Move/adapt them into a portable repository test harness before relying on repeatable browser regression coverage.
- Production build passed in an earlier Step D verification; it was not rerun after the follow-up corrections. Expired **absolute refresh-session** recovery was not independently exercised in the browser run; access-JWT expiry was.
- No live Azure OpenAI/Pinecone ingestion verification or paid resource provisioning occurred.

Backend rerun from the checkout root, with local Compose services running:

```powershell
$env:AUTH_TEST_MONGODB_URL = "mongodb://localhost:27017/?directConnection=true"
$env:AUTH_TEST_REDIS_URL = "redis://localhost:6379/15"
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q -o pythonpath=backend
.\backend\.venv\Scripts\ruff.exe check backend
```

Frontend checks from `frontend/`: `npm run lint`, `npm run typecheck`, and an isolated `npm run build`. Keep production build output separate from a running development server's `.next` output. Use the README for locked environment setup; do not assume virtual environments are present after cloning.

## Document upload and ingestion: current checkpoint

The user authorized this feature and asked to resume implementation. Work is on branch `dev` in the Desktop application checkout. The prior local README change and untracked repository instructions/tracker were preserved; no commit or push was made.

Implemented in the current working tree:

- PDF, DOCX, and UTF-8 TXT upload validation, safe names, 20 MB limit, signature checks, DOCX ZIP expansion limits, and SHA-256 visibility-scope duplicate detection.
- Manager/Admin upload through Next.js UI, authenticated source download, role/visibility ACLs, same-department private sharing controls, paginated listing, soft deletion, permission updates, reprocessing, idempotency keys, and Redis rate limits.
- Private Azurite/Azure Blob originals, Mongo document/job indexes and durable dispatch records, Redis/Celery worker and five-minute scheduler recovery.
- Text extraction, OCR_REQUIRED for scanned PDFs, configurable 600-token/100-overlap chunks, Azure OpenAI embedding client, Pinecone staged namespaces, vector-count/dimension verification, atomic active-version switch, and recovery-aware old-version/deletion cleanup.
- Safe named ingestion stages, failure summaries, metadata-only audit events, Admin job queue/filter/retry UI and APIs. Only one active job per document is allowed; reprocess version assignment is transactionally serialized.
- CORS support for the Idempotency-Key upload header and local Mongo replica-set default required for transactions.

Files changed include `.env.example`, `README.md`, `backend/app/auth/errors.py`, `backend/app/config.py`, `backend/app/main.py`, `backend/app/workers/celery_app.py`, `backend/app/documents/`, `backend/app/ingestion/`, backend dependency manifests/lock, `docker-compose.yml`, `frontend/app/documents/`, `frontend/app/manager/upload/`, `frontend/app/admin/`, and frontend shared components/styles. No application API key or user document was read or copied into source control.

Configuration required for actual embeddings/indexing: Azure OpenAI endpoint, key, embedding deployment and dimension; Pinecone API key and index name/dimension. The SDD selects Azure OpenAI for embeddings. No Azure/Pinecone account or cloud resource was provisioned, no provider request was made, and no provider secret is present in the template. With provider settings blank, an upload is stored locally and the job fails safely with `INDEX_PROVIDER_UNCONFIGURED`; Admin can retry after configuring the providers.

Fresh verification on 28 October 2026:

- Frontend ESLint: passed.
- Frontend TypeScript check: passed.
- Python `compileall` syntax check: passed.
- `uv lock --check --directory backend`: passed; 91 packages resolved without changing the lock.
- `docker compose config --quiet`: passed.
- `git diff --check`: passed (Git printed line-ending normalization warnings only).
- Python app/OpenAPI import check could not run because the local root virtual environment does not contain FastAPI. The Ruff executable/module is not installed in that environment.
- Backend test suite, Docker Compose startup, HTTP API flows, Azurite/Celery recovery, and live Azure OpenAI/Pinecone indexing were not run in this turn. Do not infer those passed.

Next verification steps when the backend environment and Docker are available:

1. From `backend/`, run `..\.venv\Scripts\uv.exe sync --frozen --extra dev`, then Ruff and backend tests (after explicitly choosing to run tests).
2. Validate `docker compose config --quiet`, then rebuild/start Compose and confirm API, worker, scheduler, MongoDB, Redis and Azurite health.
3. Configure private `.env` Azure OpenAI/Pinecone values and confirm embedding deployment/index dimensions agree.
4. Bootstrap/sign into a Manager and Admin, verify upload/list/source ACLs and Employee upload denial, then observe a successful ingestion job and controlled retry.
5. Add/rerun backend security and recovery tests for duplicate scope isolation, malformed/oversized uploads, unauthorized source access, lease/fencing recovery, worker retries, reprocessing with old-version retention, deletion cleanup and API throttling.

Approved requirements to carry forward:

- Azure OpenAI embeddings; deployment name, endpoint, dimensions and API settings must be configured explicitly. No live provider calls or cloud provisioning are implied by this tracker.
- Manager/Admin upload of PDF, DOCX and TXT up to 20 MB, with MIME/content validation, safe filenames and decompression/malformed-file protection.
- Managers manage their own uploads; only Admin publishes organization-wide. Private sharing stays within the uploader's department. Server-side ACLs control listing, metadata, original downloads and later retrieval.
- SHA-256 duplicate scopes from the full SDD: PRIVATE uploader, DEPARTMENT department, ORGANIZATION organization; prevent existence leaks across unauthorized scopes.
- Originals in private Azure Blob Storage (Azurite locally); authoritative document metadata and durable ingestion jobs in MongoDB.
- Celery/Redis dispatch with three total processing attempts, leases/fencing, idempotency and recovery reconciliation. Redis is not the authoritative job ledger.
- Extract text; scanned PDFs without meaningful text become `OCR_REQUIRED`. OCR remains deferred.
- Configurable starting chunking near 600 tokens with 100 overlap; preserve page metadata where available.
- Stage and verify a complete new Pinecone index version before atomic activation; keep an existing ready version available during reprocessing. Soft deletion blocks access immediately, followed by bounded cleanup.
- Show named processing stages and safe failure summaries, without invented progress/confidence percentages.

Follow-up feature verification remains: add/run the backend acceptance and recovery tests listed above when explicitly requested. Keep FastAPI as the only owner of authorization and document-processing logic; do not add application logic to Next.js API routes.

## Local embeddings checkpoint — runtime verified locally

28 October 2026: local CPU embeddings were added on branch `dev` without a
commit or push. The selected provider defaults to
`sentence-transformers/all-MiniLM-L6-v2`, with 384 dimensions and Pinecone
cosine similarity. Azure OpenAI remains selectable through
`EMBEDDING_PROVIDER=azure_openai`.

Implementation and corrections in the current working tree include:

- lazy, process-local `SentenceTransformer` loading on CPU;
- local batch size 8, one Celery worker process and prefetch multiplier 1;
- provider-specific tokenization and input-limit-aware chunking;
- default local chunks of 200 content tokens with 40-token overlap;
- explicit rejection when configured chunks exceed the provider limit;
- a persistent Docker Hugging Face cache with Xet disabled, while normal TLS
  certificate verification remains enabled;
- an embedding worker probe used to verify execution through Redis in the
  actual Celery child process;
- MongoDB-compatible active-document partial index syntax; and
- document-module Ruff corrections, including the missing `blob_container`
  import and logged task/recovery boundary exceptions.

The first host model download appeared stalled because stale Hugging Face lock
files remained without an owning process. A parallel retry recreated lock
contention and also began fetching unnecessary alternate model formats. The
successful retry removed only unowned locks, kept TLS verification enabled,
disabled Xet, used one download worker and selected the required PyTorch model,
tokenizer and SentenceTransformers files. The Docker worker then downloaded
the same model into its named cache normally over HTTPS.

Fresh verification on 28 October 2026:

- `uv lock --check`: passed, resolving 118 locked packages without changing
  the lock; test and lint commands used `uv run --frozen`.
- Full backend Ruff: passed with no findings.
- Full backend suite with the Compose MongoDB replica set and Redis enabled:
  **98 passed in 190.98 seconds**; no dependency-unavailable skips remained.
- Host CPU runtime: model and tokenizer limits are both 256 tokens; two special
  tokens leave 254 content tokens. A 500-token input at 200/40 produced content
  chunks `[200, 200, 180]` and final model inputs `[202, 202, 182]`, all within
  the 256-token limit. A real normalized embedding contained 384 values.
- Compose validation passed. MongoDB, Redis, Azurite and API were healthy;
  scheduler and worker were running. API liveness returned 200. Readiness
  transiently returned 503 while the CPU and disk were saturated by tests and
  initial model loading, then recovered to 200 with MongoDB, Redis and Azurite
  all reported ready.
- Celery reported concurrency `1 (prefork)`. The first worker embedding probe
  downloaded/loaded the model and returned local provider, dimension 384,
  content limit 254 and batch size 8. A second probe in the same worker process
  completed in about 0.06 seconds, confirming process-local model reuse. The
  `scaffold_ping` Redis round trip returned `worker-ready`.

Not verified: no Pinecone credentials were used and no live Pinecone index,
upsert, vector-count check or end-to-end ingestion succeeded in this work. The
optional Azure OpenAI provider was not called. Frontend checks were outside
this backend/local-embedding completion request.

Next step: create a Pinecone index with **dimension 384** and **cosine** metric
in the desired cloud/region, configure its private index name and API key, and
run a controlled ingestion smoke test. Do not mark Pinecone integration passed
until upsert, count/dimension verification and retrieval are observed.

## Pinecone index setup — 28 October 2026

User authorized this chat to perform the index setup directly in the Desktop checkout on dev.
Created enterprise-ai-local through the installed Pinecone SDK using the existing private key.
Verified Ready, dense vector API compatibility, 384 dimensions, cosine metric, AWS us-east-1,
serverless managed deployment and zero stored vectors. No integrated embedding model was configured.
Set PINECONE_INDEX_NAME=enterprise-ai-local and EMBEDDING_PROVIDER=local in ignored .env;
existing secrets were preserved and never printed. No billing upgrade, commit or push was made.
API, worker and scheduler were recreated to load configuration. Full document ingestion/upsert,
activation and vector fetch still need a controlled document test; index connectivity alone
is not evidence of successful ingestion.

## Manual browser testing attempt — 29 October 2026

The existing Chrome Admin session was readable and the Documents page was reached.
Observed the empty-document state and disabled Upload and process button without a selected file.
Created synthetic TXT/PDF/DOCX and invalid-file fixtures in the separate Documents helper
folder test-fixtures; no real company documents were used. Browser mouse targeting failed
with unavailable coordinate geometry, and screen capture timed out after recovery attempts.
No upload, ingestion, permission mutation, or source download was completed. These tests
remain pending; do not infer an end-to-end ingestion pass from this attempt.

## Open issues and limits

- Make browser regression coverage portable and verify absolute session expiry in the browser.
- Local embedding runtime, service-backed tests, and live Pinecone ingestion have evidence below; optional Azure OpenAI integration remains untested.
- Manager/Admin permission regression tests and user-confirmed Employee/private-sharing browser checks passed as recorded below; this does not replace production load and deployment validation.
- The previously tracked development lint-chain advisory and ESLint peer-range warnings remain recorded in `implementation-plan.md`; their current upstream status was not rechecked in this session.
- Signing-key storage/rotation, retention policies, hosted cookie topology, backup/restore, observability and deployment hardening remain production prerequisites. Do not invent approved values.
- `.env`, credentials, API keys, passwords, JWTs, refresh tokens and sensitive document content must never appear in this tracker or Git history.

## Updating this tracker

At each feature checkpoint, update the current state, actual scope delivered, decisions, affected modules, verification commands/results/date, remaining failures, next actions and last feature commit. Mark unrun, skipped, mocked and live-provider checks distinctly. If work stops midway, record what is incomplete and how to resume; do not label it complete.

Include this tracker in the feature's Git commit on `dev`. When committing, record the preceding verified commit here and use Git history for the new commit ID; avoid amending repeatedly just to embed a commit's own hash.

A file or local commit alone does not protect against laptop loss. Push approved checkpoints to GitHub and verify the remote contains the intended branch. Never claim a push/backup succeeded without checking it; request user authorization before pushing when it has not already been given. Keep credentials and provider data backups separate from source control.

## Upload failure fix and resumed verification — 30 October 2026

Fixed the reported POST /api/v1/documents HTTP 500: added the Azure Blob aio dependency extra, used ContentSettings for upload MIME metadata, and configured storage API version 2023-11-03 for Azurite compatibility. Unexpected errors now return sanitized JSON with request IDs through the CORS boundary. Added focused upload regression coverage and README notes.
Earlier focused verification: five tests passed, including real Azurite upload/read/content-type/delete; Ruff and lock checks passed. The deployed backend images were updated incrementally from the existing local base using the frozen lock; a clean full rebuild was interrupted and is not claimed complete.
On resuming, Docker was stopped. Started Docker Desktop and existing Compose services without rebuilding; API, MongoDB, Redis and Azurite became healthy; frontend, worker and scheduler started.
Read-only sample verification: ingestion-smoke-test.txt is READY, active index version 1, chunk count 1; latest job SUCCEEDED / COMPLETE. Direct Pinecone vector fetch and a fresh full backend suite are still pending at this checkpoint. No credentials or document text were printed. No commit or push was made.
Direct Pinecone verification subsequently passed: namespace count 1, deterministic vector fetch found, index and stored vector dimension both 384. This confirms the sample TXT ingestion through vector storage; semantic query relevance and remaining browser acceptance cases are not yet verified.
PDF manual acceptance check (30 October 2026): user uploaded synthetic qa-support-policy.pdf through Chrome. Screenshot showed POST 202 and polling 200. Database subsequently confirmed READY, one chunk, active version 1, job SUCCEEDED/COMPLETE on attempt 1. Worker logged successful Pinecone upsert and task completion in 83.22 seconds; model initialization occurred during this task. DOCX, invalid-file, source-download and permission browser checks remain pending.
DOCX manual acceptance check (30 October 2026): user uploaded synthetic qa-training-policy.docx in Chrome; POST returned 202 and polling 200. Read-only database check confirmed READY, one chunk, active index version 1, job SUCCEEDED/COMPLETE on first attempt. Screenshot also showed a stale QUEUED list row while the progress banner had advanced to indexing; refresh/poll synchronization needs review. Invalid-file, source-download and permission browser checks remain pending.

## Document permission regression and status refresh — 30 October 2026

User-confirmed Chrome acceptance results: duplicate rejection 409, empty-file rejection 422, disguised PDF rejection (user report), oversized rejection 413, unsupported CSV rejection 415, readable PDF source download, Engineering documents hidden from Finance and visible after department reassignment, Employee Admin page restriction, private sharing grant/revoke visibility and authorized download.
Added backend/tests/test_document_permissions.py: direct ASGI HTTP requests through the real FastAPI application, real Admin-created accounts/login/session validation, MongoDB replica-set transactions and Redis in a uniquely named isolated test database. Blob upload/source content and Celery dispatch are test doubles; no real user documents or credentials are used and no live Pinecone ingestion is claimed by this test.
Fresh permission regression: 1 comprehensive test passed in 115.25 seconds. Assertions cover Employee upload denial; unshared private source denial; denied access changes; Manager upload/download/access change/delete of own documents; denied mutations/reprocess/delete on another uploader's readable document; denied organization publishing; Employee/Manager Admin user and ingestion endpoints denied; private revocation effective with an existing Employee token. Unauthorized document requests return 404 to avoid resource disclosure; role-only endpoints return 403. Existing backend logic passed without changes.
Fixed frontend/src/components/document-workspace.tsx: refresh document rows quietly alongside active job polls, poll pending documents after page reopen, cancel polling requests on cleanup, prevent overlapping pending-list refreshes, and remove unrelated job stage fallback. Backend document metadata remains the displayed source of truth.
Frontend full lint and TypeScript checks passed before the final non-overlapping polling cleanup; final targeted lint/typecheck rerun pending at this checkpoint. New test Ruff passed. Live visual verification of the updated polling behavior remains pending a fresh upload in Chrome. No production build or full backend suite completion is claimed for this checkpoint. No commit or push made; branch dev preserved.
Final checks after polling cleanup passed: targeted ESLint for document-workspace.tsx and TypeScript tsc --noEmit both exited 0; git diff --check passed with existing CRLF normalization warnings only.


## Uncommitted upload/ingestion code review — 30 October 2026

Reviewed current `dev` working tree without application code changes or commits. Full existing backend suite: **101 passed in 400.64 seconds**. Backend Ruff, frontend ESLint/TypeScript and diff checks passed. The Azurite synthetic transport test was enabled; no live AI/vector provider call was made.

Four focused probes reproduced stale-ACL revocation reversal, unbounded pre-auth multipart consumption, permanently stranded QUEUED/DISPATCHED jobs, and a concurrent cleanup/late-upsert vector leak. Static review also found both document/job UIs discard pagination cursors. These cases are not covered by the passing existing suite. Fix and regression-test them before the completion commit.

Details, priorities, verification limits and next actions: [review-document-ingestion-2026-10-30.md](docs/review-document-ingestion-2026-10-30.md). The report and this tracker update remain uncommitted with the feature changes.

## User acceptance and recovery observation — 1 November 2026

User reports the retrieval manual checklist otherwise passed. E1 showed the intended busy/unavailable response with worker stopped. E2 remains pending: after restart, the second screenshot still shows Document search is temporarily unavailable. Logs distinguish a 118.59-second model cold load (versus 20-second query wait), Hugging Face DNS errors, and a later API PineconeConnectionError/503 despite subsequent worker embeddings taking about 0.04–0.05 seconds. Do not classify the later provider failure as just warm-up or mark recovery passed. No data-loss evidence was observed. Detailed operational limits and proposed readiness/offline-cache review actions are in docs/retrieval-known-limitations.md. No code, private configuration, document ACLs, commit or push changed in this observation.
