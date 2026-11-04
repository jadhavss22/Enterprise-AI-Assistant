# Retrieval review fixes — 2 November 2026

Baseline: a65d19c on dev. This change fixes R1-R5 from review-retrieval-2026-11-01.md. No generation/chat functionality, dependencies, secrets, main-branch changes, commit or push are included.

## R1: actual provider work is bounded

A dedicated process-wide ProviderPool has the configured retrieval_search_concurrency capacity. Each concurrent future owns its permit until the underlying synchronous operation actually finishes (or is cancelled before starting). Async/HTTP cancellation does not release a running call's permit. Index lookup, namespace queries, worker publish/result waits/cleanup, and vector probes use this pool instead of occupying the default executor without accounting. Busy admission fails explicitly; sibling failure and repeated timeout regressions verify capacity is retained and restored on actual completion. This is a per-API-process limit, not a cluster-wide limit.

## R2: active-version count remains consistent

Pending chunk count is written to the ingestion job as pending_chunk_count. Document chunk_count remains coupled to active_index_version until both are published in the existing activation transaction. A real isolated MongoDB worker regression rechunks to fewer chunks, fails embedding, verifies the old active passage remains valid, then verifies successful activation changes version/count together. No production document metadata was rewritten; historical inconsistent records, if discovered, require controlled repair/reprocessing.

## R3: deliberate execution-child warm-up and cache-only mode

Celery worker_process_init starts warm-up in the execution child on a background thread, returning promptly to satisfy Celery's initializer timing constraints. A synthetic embedding validates the loaded provider before it is marked ready. Query tasks only obtain the ready provider; they never initialize it. A separate child heartbeat advertises warming/ready/failed in Redis, scoped by a provider-profile fingerprint, with 15-second expiration and three-second refresh. Shutdown removes the key best-effort; a killed child expires. No raw queries, vectors, keys or provider error details are stored in readiness records.

LOCAL_EMBEDDING_CACHE_ONLY defaults true. SentenceTransformer receives local_files_only, and Compose sets HF_HUB_OFFLINE consistently for the worker. Missing/invalid cache fails explicitly instead of silently downloading or claiming readiness. Provisioning is an explicit separate command, followed by worker restart. Cold CPU imports/loading still take time; this change makes readiness truthful rather than promising instant startup. Current hooks target the approved Docker prefork worker; another pool implementation needs its own execution-process initialization validation.

## R4: retrieval readiness is separate from core health

Authenticated GET /api/v1/retrieval/readiness returns a 200 status resource containing status, model, vectors, code, retry_after and request_id. Clients must inspect status; it is not a replacement for core /health/ready. Redis state lookup is bounded; vector checks use a single-flight five-second cache and the bounded provider pool. The configurable vector-readiness budget is eight seconds, covering remote index lookup plus stats. An initial three-second budget was too tight (a direct check measured 2.97 seconds without async overhead); the final eight-second check reported ready. Vector dimensions must match the configured embedding model.

Queries fail closed before publishing if the model is warming, failed or unavailable. Core API readiness continues to cover MongoDB/Redis/Azurite, preserving login/listing availability independently. Heartbeat expiration can leave a short stale interval after abrupt termination; query timeout rechecks model readiness before reporting busy.

## R5: actionable UI state and retry semantics

The Documents panel checks readiness and polls warming/unavailable states without automatically submitting questions. It distinguishes stopped worker, warming, missing cache/configuration and vector-service outage; provider failure is never described merely as warming without readiness evidence. Structured search error codes, safe request IDs and Retry-After cooldowns remain visible. User cancellation and the browser deadline have different messages/codes. Changing identity/department/role remounts the search panel so previous results/input are not reused between identities. Downloads still reauthorize at the backend.

## Fresh verification

- Full backend suite: 126 passed in 347.25 seconds. After the final readiness-budget regression was added, the focused retrieval/hardening suite passed 14 tests in 23.31 seconds. The full-suite count does not include that one later-added test.
- Backend Ruff, final frontend full ESLint/TypeScript, unchanged dependency lock (126 packages), and diff checks passed.
- Isolated webpack production build passed. Headless production search UI regression passed warming/vector gates, source/empty/error states, Retry-After countdown and request ID, separate user cancellation and a real 50-second client timeout, and revoked downloads. Existing pagination regression verification is recorded in PROJECT_STATUS.md.
- A temporary container with networking disabled and read-only code/cache mounts loaded the cached model and produced 384 dimensions. A deliberately nonexistent model failed with MODEL_CACHE_UNAVAILABLE. Combined run took 86.83 seconds; no model download or TLS bypass was used.
- Live execution-child startup was observed warming and then logged model_ready before any search query triggered loading. A real worker-stop check returned WORKER_UNAVAILABLE while core /health/ready remained ready. The worker was restarted in a finally block; retrieval subsequently reported model/vector ready. One authorized synthetic search then returned MATCHES through the real worker/Pinecone path with disposable auth/database state.
- Full eight-question live synthetic calibration also returned HTTP 200 for all cases, found the expected source documents for all six positive questions, and returned empty results for both unrelated questions at cutoff 0.25. This tiny reused dataset is not a production or holdout quality guarantee.
- Provider outage/recovery and deadline interleavings used doubles; we did not disable the user's internet or intentionally interrupt a hosted provider. Existing documents, ACLs and vectors were not changed by verification.

## Provision once if the local cache is absent

From the repository root in PowerShell:

```powershell
docker compose exec -e HF_HUB_OFFLINE=0 -e LOCAL_EMBEDDING_CACHE_ONLY=false worker python -m scripts.provision_embedding_model
docker compose restart worker
```

This requires internet access for the explicit download. Normal worker startup remains cache-only. After changes to Compose environment, use docker compose up -d --no-build worker (or rebuild when dependencies change). After configuration/profile changes, recreate the applicable API/worker containers together. Never commit private .env values or copy them into test reports.
