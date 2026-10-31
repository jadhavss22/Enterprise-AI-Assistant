# Review: uncommitted document upload and ingestion

Reviewed 30 October 2026, branch `dev`, against feature baseline `2a8369e`.
Application code was not changed and nothing was committed or pushed by this review.

## Conclusion

Corrections are required before treating this feature as ready for its completion commit. Successful TXT/PDF/DOCX ingestion and ordinary role/ACL checks do not cover the failure/concurrency cases below.

## Findings

1. **P1 — A stale permission PATCH can restore revoked access.**
   `backend/app/documents/service.py:428-432,489-495` computes both visibility and allowed users from the document fetched before the transaction. Although the transaction reads `current_doc`, it still writes both old computed fields. Reproduced with two request snapshots: revoke a shared user, then commit a visibility-only PATCH that read the old snapshot; the revoked user is restored. Compute omitted fields from the transaction's current document, and validate the resulting ACL in that transaction; alternatively require a conditional ACL version and reject stale writes.

2. **P1 — The upload size bound runs after unbounded multipart parsing.**
   `backend/app/documents/routes.py:57-73` declares a `File()` body and reads only 20 MB + 1 in the handler, but FastAPI has already consumed/spooled the multipart body before entering the handler or its authentication dependencies. A request to the actual app without credentials streamed a full 21 MiB file before returning 401. There is no ASGI/request-body cap in the current app or Compose proxy configuration. Add an early streaming request-size budget (including multipart overhead), handle absent/misleading Content-Length, and reject oversized bodies before they consume arbitrary temporary disk space. Keep the per-file check as well.

3. **P1 — Lost dispatched ingestion jobs are never recovered.**
   `backend/app/ingestion/tasks.py:507-514` reconciles only QUEUED/PENDING jobs and expired PROCESSING jobs. If Redis loses a published message before a worker claims it, the durable Mongo record stays QUEUED/DISPATCHED without a lease. Reproduced an aged two-hour record: reconciliation did not redispatch or fail it. Add a bounded stale-dispatch watchdog and idempotent redispatch, using Mongo lease/claim state to prevent duplicate processing.

4. **P2 — Concurrent deletion cleanup can finish before an in-flight upsert recreates the namespace.**
   `backend/app/ingestion/tasks.py:106,777-817` allows an upsert to finish after another worker deletes its namespace and marks cleanup SUCCEEDED. The next Mongo fencing check blocks activation, but cannot undo the external vector write; successful cleanup records are not revisited. The focused interleaving test left one vector after cleanup SUCCEEDED. This requires overlapping processing/cleanup workers or a failover overlap; the current single-concurrency local worker normally serializes these tasks. Coordinate cleanup with outstanding write attempts and reconcile late/orphan namespaces; checking a fence only after upsert is insufficient.

5. **P2 — The document and Admin job screens discard pagination cursors.**
   `frontend/src/components/document-workspace.tsx:60-62` and `frontend/src/components/admin-ingestion-jobs.tsx:24-25` always fetch the first 50 rows and ignore next_cursor. Older accessible documents and failed jobs become unreachable through the UI after the first page fills, including older job Retry actions. Store/use the cursor and expose navigation or Load more; integrate polling with the selected page.

## Verification

- Full existing backend suite: **101 passed in 400.64 seconds**. Local MongoDB replica-set and Redis test URLs were set, and the synthetic Azurite regression was explicitly enabled. The tests use disposable databases/Redis keys and clean their synthetic Blob fixture.
- Full backend Ruff, frontend ESLint, frontend TypeScript (no emit/incremental output), and `git diff --check` passed. Existing line-ending normalization warnings remained.
- Four focused review probes reproduced findings 1–4 against the actual application services/tasks and real disposable MongoDB. Broker, Blob and Pinecone operations were test doubles. No live provider requests or real uploaded documents were involved. The 21 MiB body probe used the actual FastAPI upload route without credentials.
- The focused probe script is in the separate helper workspace, `Documents/ChatGPT/Enterprise-AI-Assistant/review_ingestion_cases.py`; it is not a portable repository regression suite. Add proper failing regressions for the confirmed cases when fixing them.
- No production build, new browser acceptance run, Azure OpenAI call or live Pinecone re-ingestion was performed for this review. Existing test success does not cover the confirmed bugs above.

## Resume

Fix findings 1–3 first, add regression tests, then address concurrent cleanup and UI pagination. Re-run relevant checks and update PROJECT_STATUS.md before the feature checkpoint on `dev`. Preserve the user's existing uncommitted work.

## Resolution implemented — 31 October 2026

The working tree now contains corrections for all five findings: omitted ACL fields are resolved from current transaction state; an ASGI upload-body budget runs before multipart parsing and covers absent/misleading Content-Length; stale QUEUED/DISPATCHED jobs are eligible for redispatch; index-write intents coordinate deletion cleanup and reopen cleanup after a late write; document/job UIs expose cursor-based Previous/Next controls while preserving the selected page during refresh. Portable backend regressions are in backend/tests/test_document_review_fixes.py. A standalone frontend pagination browser harness is in frontend/tests/pagination-smoke.cjs (requires an externally provided Playwright installation).

This resolution does not establish production load limits, OCR, Azure OpenAI integration, or permission-aware semantic retrieval. Fresh checkpoint verification is recorded in PROJECT_STATUS.md; the original findings above remain as historical context.

Checkpoint verification (31 October 2026): full backend suite 113 passed in 293.71 seconds, backend Ruff, frontend lint/TypeScript, lock and diff checks passed. The initial findings are historical and their implemented resolutions above are included in this checkpoint; production deployment/load validation remains separate.
