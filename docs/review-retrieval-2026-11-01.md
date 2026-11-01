# Permission-aware retrieval code review

Reviewed 1 November 2026 on `dev`, against baseline `8716964` and the current uncommitted retrieval slice. Application code was not changed. No commit, push, live provider call, service restart or private configuration change was made by this review.

## Findings

### R1 — P1: Provider work outlives the concurrency permit

`backend/app/retrieval/providers.py:116-138` runs synchronous Pinecone requests through `asyncio.to_thread`, then cancels their asyncio wrappers on a timeout or sibling failure. Cancellation does not stop a running synchronous request. `service.retrieve` releases its search permit immediately, so new searches can submit more provider work while old calls remain in the shared executor. A provider-double probe confirmed four provider calls still running after four returned 503s, despite two admitted search slots and one namespace request per search in the probe configuration. The Python executor still has its own maximum size; the finding is that the configured retrieval budgets do not bound the actual outstanding work, which can also occupy threads needed by other API operations.

Keep a process-wide provider-work permit until the synchronous call actually completes, or use cancellable async transport with bounded connection pools. Ensure all calls are drained/accounted for after a sibling fails, and test repeated timeout/failure cycles rather than only successful searches.

### R2 — P2: Reprocessing changes the chunk bound of the old active version

`backend/app/retrieval/service.py:71` rejects matches with a chunk index outside the document's current `chunk_count`. However, `backend/app/ingestion/tasks.py:389-391` writes the new processing attempt's chunk count before the atomic active-version switch. If rechunking produces fewer chunks, valid passages from the old active namespace disappear while reprocessing runs; they remain suppressed if reprocessing subsequently fails. A focused snapshot probe accepted active version 1/chunk 2 with count 3, then rejected the same candidate with active version still 1 after the pending count became 1.

Keep pending chunk counts on the job or version manifest, and publish the active version and its corresponding count together during activation. Add a regression that changes chunking and fails reprocessing while checking that old-version passages remain searchable. The existing PROCESSING-state retrieval test leaves the chunk count unchanged and therefore misses this case.

### R3 — P2: Startup warm-up still happens inside the first user query

`backend/app/retrieval/tasks.py:15-19` constructs the lazy embedding provider in the user-query task. The worker has no startup warm-up hook/readiness gate. The recorded cold load took 118.59 seconds, while the query bridge waits only 20 seconds. Initial requests consequently time out and other queued queries may expire while that first task loads the model. Increasing query timeouts alone would conceal the startup problem and increase contention with ingestion.

Warm the model deliberately in the Celery execution process, expose initialization success/failure, and gate query admission appropriately. A warm-up in the API or Celery parent process does not establish child-process model readiness. The existing loader also has no explicit validated cache-only setting: the recorded Hugging Face DNS retries need a controlled offline-cache path after provisioning, without disabling TLS or pretending a missing cache is ready.

### R4 — P2: Model/retrieval readiness is not represented by health checks

`backend/app/main.py:94-97` and `backend/app/infrastructure/health.py` report core MongoDB/Redis/Azurite health, but do not establish that a Celery execution process has a usable model or that vector search is available. A container/pong/core-ready response can coexist with a cold model, missing worker or failed vector connection. Keep core API health available for login/listing, but expose a separate, bounded retrieval-readiness signal with explicit warming, ready and failed/unavailable states. Do not perform an expensive model load on every health request or treat worker liveness as a readiness substitute.

Recovery case E2 remains open from the user's observation. The later PineconeConnectionError occurred after embeddings were fast and must be verified separately; warm-up alone does not diagnose or fix it. A successful authorized search after restoring connectivity is required to close E2.

### R5 — P2: Retry messages discard actionable state and diagnostics

`frontend/src/components/document-search.tsx:33-34` keeps only an error message, even though the shared API error includes code, requestId and retryAfter. The user cannot see the rate-limit wait or a request ID for reporting the Pinecone failure, and the same immediate retry interaction is offered for warming, provider outage and configuration failures. Backend `embed_query` also collapses all provider initialization failures to EMBEDDING_UNAVAILABLE, so no reliable warming/failed distinction currently reaches the UI.

Preserve structured errors. Show truthful warming/busy guidance only when readiness supports that diagnosis; show rate-limit timing/countdown from Retry-After; distinguish unavailable/configuration errors where reliably known; expose the safe request ID; and keep cancellation separate from timeout. Do not tell users that a confirmed vector-provider connection failure is only model warm-up.

## Verification and boundaries

- Fresh focused backend suite: **5 passed in 22.19 seconds** (`backend/tests/test_retrieval.py`) with real isolated MongoDB/Redis authentication/ACL state and worker/vector test doubles.
- Fresh full backend Ruff, frontend ESLint, TypeScript (`tsc --noEmit --incremental false`) and `git diff --check` passed. Git emitted only the existing tracker line-ending normalization warning.
- Focused provider-double probes confirmed R1 and R2. The scripts are in the separate helper workspace as `review_retrieval_cases.py`, not committed repository tests. No real vectors, user documents or provider credentials were used by the probes.
- The reviewed ACL path scopes namespaces first and revalidates session/account, current visibility/deletion and active versions before returning passages. The existing direct-route isolation tests passed. This is not a blanket security guarantee beyond those tests and inspected paths.
- The previously recorded 118-test full suite, production build, browser smoke and synthetic live calibration are historical evidence; they were not rerun by this review.
- No fresh browser recovery run, cold-start timing measurement or live Pinecone connectivity test was performed. E2 is not closed by this review.

## Resume

Correct and regression-test R1/R2; implement the explicit startup/readiness/retry items R3-R5; verify cold-cache/warm-cache startup and offline-cache failure; then verify worker-stop/restart and independent vector-provider outage/recovery end to end. Update PROJECT_STATUS.md before the retrieval checkpoint. Query rewrite and answer generation remain later slices.
