## OCR review checkpoint - 23 November 2026

Reviewed native/scanned page selection, process resource limits, timeout/exit cleanup, extraction cancellation, progress metadata, retries, prior-version preservation, citation pages, Docker deployment, CI engine requirements, and frontend progress.

Fixed three failure paths. The parent now kills the remaining process group and reaps the helper on every exit, including abnormal helper termination. A page that consumes the remaining document budget is classified as OCR_DOCUMENT_TIMEOUT so it does not consume repeated automatic attempts. Lease-loss and document-deletion signals raised by OCR progress retain their cancellation meaning through extraction instead of becoming parse failures. Five new regression cases cover these behaviors.

Fresh verification: 225 backend tests passed in 429.19 seconds. Frontend lint, TypeScript, isolated Linux production build, and the headless Edge bulk-upload regression with synthetic OCR progress passed. Backend Ruff, focused formatting, and whitespace checks passed. OCR runs in Linux with the real Tesseract engine. The integration path uses real MongoDB/Redis and provider doubles, and the full suite includes the Azurite transport check.

The user authorized publishing this reviewed checkpoint into dev through a feature PR and both required CI jobs. This entry precedes Git operations. Representative company-scan and live cloud-provider acceptance remain pending. Deployment requires rebuilding and restarting the backend services to install the OCR engine. The existing Next.js dependency advisory remains recorded separately.

## Printed English PDF OCR - 21 November 2026

Implemented local Tesseract OCR in the Linux ingestion worker on dev. Native PDF extraction selects image-heavy pages with little text and nonempty pages with no native text. Mixed pages retain native text, and recognized text enters the existing page-specific chunking and indexing pipeline. Original one-based PDF pages remain in search results, Chat citation events, and saved conversation history.

Added OCR job progress and page counters to the upload queue and Admin ingestion jobs. Defaults allow 50 scanned pages, 20 seconds per page, 120 seconds per document OCR stage, eight million render pixels, and 768 MiB per page subprocess. Fresh Linux process groups bound memory, CPU, output, and lifetime; timeout cleanup kills and reaps the group. Errors expose fixed messages. Transient engine and per-page timeout failures use the existing three-attempt retry budget. Unreadable and over-budget scans fail explicitly and preserve any prior active version. Processing retry is available after OCR worker configuration is corrected.

Verification: All 220 backend tests passed in 317.77 seconds on 21 November with real Linux Tesseract, MongoDB, Redis, and the local Azurite regression enabled. New coverage includes synthetic scanned and mixed PDFs, all four right-angle rotations, unreadable and blank scans, smaller render budgets, confidence rejection, deadlines, process-group timeout cleanup, retries, prior-version preservation, and lease fencing. The upload-to-search-and-Chat test uses actual Tesseract and real MongoDB/Redis with blob, embedding, vector, and answer-provider doubles. No live cloud-provider or representative company-scan acceptance is claimed. The first full run had an incorrect citation-event assertion and a container loopback routing problem for the existing Azurite test; both test issues were corrected before final verification.

Backend Ruff, focused formatting, and Git whitespace checks passed. Frontend ESLint, production build, TypeScript, and the headless Edge bulk-upload regression passed on 21 November, including OCR page progress. The existing Next.js dependency security advisory remains a separate open upgrade review.

Deployment requires rebuilding and restarting the backend images to install Tesseract, English data, and orientation data. See docs/ocr-ingestion.md for settings, deployment, and verification limits. CI installs the OCR engine and requires real OCR tests. The reviewed fixes and fresh verification are recorded above. The user authorized publication; representative printed-English scan acceptance remains part of rollout. Hybrid retrieval, reranking, and query rewriting remain disabled by default pending their separate live quality evaluation.

## Retrieval quality review - 20 November 2026

Reviewed optional reranking and query rewriting, worker startup, pinned cache-only model loading, input/output bounds, provider-pool lifetime, cancellation, retry fallback, current ACL/version rechecks, and evaluation scripts. Two rewrite guard gaps were fixed. Numeric/identifier token sets must now match exactly, which rejects invented numbers and identifiers. Common negations are checked in both directions and contracted forms, including curly apostrophes and cannot, are recognized. Seven new cases cover these constraints. The guards protect specific literals and require evaluation for broader intent preservation.

Fresh verification: 201 backend tests passed in 291.84 seconds. Full backend Ruff, focused formatting, default-settings checks, evaluation CLI help, and whitespace checks passed. The initial focused run had one pytest temporary-directory access error. The complete rerun used normal temporary-directory access and local MongoDB, Redis, and Azurite. Provider/model behavior in the new quality tests uses doubles. No new live model-backed quality comparison was run.

Both features remain disabled by default. Provision and benchmark the reranker and compare baseline/reranking/rewriting/combined on labeled data before enabling. Inference shares the embedding worker, so worker contention remains part of that evaluation. User authorized committing and publishing the checkpoint into dev through a feature PR and the required GitHub checks. This entry precedes Git operations.

## Reranking and query rewriting checkpoint - 20 November 2026

Implemented opt-in quality stages in shared retrieval for Document Search and Chat on dev. Reranking uses the existing pinned MS MARCO MiniLM cross-encoder on the Celery execution worker. Startup warms a locally provisioned model cache, and queries cannot trigger downloads or initialization. Validated candidates are reordered without lowering the semantic cutoff or filtering on model logits. Optional hybrid results can also be reranked. Existing experiment callbacks retain their separate behavior.

After a successful search returns no relevant matches, optional Gemini rewriting may produce one alternative question and make one bounded retrieval retry. Literal identifiers, numbers, quoted terms, and explicit negation are guarded. Existing matches and inaccessible-document scopes bypass the stage. Chat answers and saved questions retain the user's original intent while the alternative phrasing is used only for retrieval. The existing history-based follow-up resolution continues to run.

Defaults remain disabled through RETRIEVAL_RERANK_ENABLED and RETRIEVAL_QUERY_REWRITE_ENABLED. Reranking has an eight-second budget with a separate bounded API provider pool and ephemeral, redacted task transport. Inference shares the existing Celery worker, so worker contention requires evaluation. Rewrite generation has a five-second budget and its retrieval retry has a ten-second budget. Optional failures preserve the original candidate order or empty result. Authentication failures propagate, and final source permissions and active versions are rechecked.

Added explicit reranker model provisioning, setup instructions in docs/retrieval-quality.md, and a four-profile synthetic evaluation runner. The runner records optional-stage execution counts and fails if an enabled model stage is never exercised. Live model-backed comparison has not run for this implementation. Provision the pinned cache and evaluate on labeled questions before enabling either stage. The shared path performs at most one alternate search. Review confirmed that dev has no chat-only retry implementation, and Chat makes one shared retrieval call. The separate earlier prototype must not be stacked onto this path.

Verification on 19 November passed all 21 new quality-stage checks and 26 existing retrieval, hybrid, hardening, fallback-experiment, and Chat checks against the local MongoDB replica set and Redis with model/provider doubles. The first integrated attempts encountered stopped services and two incorrect fixture assumptions, which were resolved before the passing checks. The new evaluation CLI imports and displays help successfully. Fresh checks on 20 November passed backend Ruff, focused formatting, and Git whitespace checks. No live quality improvement is claimed.

Review and verification are recorded in the newer entry above. The user authorized publication into dev through required CI.

## Bulk upload review checkpoint - 19 November 2026

Reviewed the multi-file queue, identity/session guards, XHR transfer progress, cancellation, stable upload keys, processing retry reconciliation, polling, permissions, and both upload entry points. Fixed two failure paths. Unexpected empty success responses now reject cleanly and incomplete acceptance IDs cannot leave a file stuck in processing. A rate-limit response pauses the unsent remainder of the batch with per-file retry actions, preserving each file and its original key and visibility.

Fresh checks passed frontend ESLint, TypeScript, and the exact production build in an isolated Linux Node 22 container. Headless Edge against that production build passed mixed file validation, sequential uploads, lost-response upload and reprocess recovery, session refresh, request/rate-limit errors, no further transfers after 429, empty and incomplete acceptance recovery with stable keys, pagination, role controls, both upload entry points, and mobile layout. Browser API responses were synthetic. Manual provider-backed upload acceptance remains pending. The existing Next.js advisory requires its separately recorded dependency review.

User authorized publishing this checkpoint into dev. Work uses codex/bulk-upload-review and a PR so both required GitHub checks can run before merging. This entry precedes Git operations. Next step is manual acceptance with real uploads and the separately tracked dependency upgrade.

## Bulk document upload - 19 November 2026

Implemented multi-file selection in the existing Documents and Manager Uploads entry points on dev. Each file shows byte-transfer progress, server validation wait, ingestion stage and attempt count, terminal status, and its own errors. Uploads run sequentially and capture each file's visibility and stable idempotency key. Failed transfers can be retried with the same key. Failed processing retries use the existing owner/Admin reprocess endpoint and check job history first to recover an accepted retry whose response was lost.

The queue exists in page memory and is cleared on navigation/reload. The UI tells users to keep the page open for batch tracking and retry. Accepted documents continue to use durable backend ingestion jobs. Client checks reject unsupported extensions, empty files, and files larger than 20 MB. The existing API remains authoritative for file content, roles, visibility, duplicate detection, and upload limits. OCR-required failures show the existing error without a processing retry action.

Added authenticated XMLHttpRequest uploads for transfer progress, session refresh on HTTP 401, abort on unmount, a two-minute transfer timeout, and request ID/Retry-After error display. Polling failures remain visible and can recover. The latest document-page refresh callback is used when uploads finish during pagination.

Frontend ESLint, the production build, and TypeScript passed on 18 November. The final headless Edge regression against that production build passed on 19 November using synthetic API responses. Coverage includes mixed valid/invalid batches, sequential transfers, stable-key retry after a lost upload response, processing retry without re-upload, lost retry response recovery, session refresh, rate-limit display, visibility preservation, document pagination during upload, Manager/Employee/Admin controls, both upload entry points, and mobile overflow. Manual production-provider acceptance is pending.

The original implementation was reviewed on 19 November. See the review checkpoint above for the fixes and fresh verification.

Next.js reported a security advisory for the existing installed version during verification. That dependency issue remains open for a separate upgrade review.

## Hybrid retrieval checkpoint - 18 November 2026

User authorized committing and pushing the reviewed hybrid retrieval feature and runtime fixes to dev. The implementation, evaluation runners and results, regression tests, and review report form this checkpoint. Verification from the review passed 26 retrieval and Chat tests, backend Ruff, focused format checks, and 52 live searches with zero keyword fetch errors. Hybrid retrieval remains disabled by default pending broader relevance evaluation. This entry precedes Git operations; verify the resulting commit and remote hash with Git.

## Hybrid retrieval review - 18 November 2026

Reviewed the opt-in retrieval path, provider deadlines and cancellation, authorization and active-version rechecks, keyword limits, fusion, and evaluation runners against dev.

Two runtime findings were corrected. The keyword pass constructed a second Pinecone client and resolved its host inside the four-second deadline. Five read-only timing samples on the synthetic fixtures measured client setup at 2.88–4.65 seconds, with fresh fetches around 1.0–1.13 seconds. Retrieval now passes the semantic search's index client into the keyword pass.

Keyword fetches also shared the required provider pool. A synchronous fetch can continue after asyncio cancellation and retain its permit, potentially making later semantic requests return PROVIDER_BUSY. Optional keyword calls now use a separate bounded pool, retain their permits until synchronous completion, and pass the remaining overall deadline to each fetch.

The regression tests verify that the keyword pass receives the same index client and that a timed-out keyword call retains its own permit while the semantic provider pool remains available. Existing source authorization and active-version checks still run after both retrieval legs.

Fresh verification passed all 26 retrieval, keyword, hardening, fallback experiment, and Chat integration tests against the local MongoDB replica set and Redis in 161.34 seconds. Backend Ruff and focused format checks passed. Initial runs encountered stopped test services, which were restored before the passing run.

The corrected live benchmark ran inside the API container with the isolated embedding worker and three existing synthetic documents. All 52 measured searches returned HTTP 200. All 26 hybrid keyword passes completed without fetch errors, compared with 23 deadline failures in the earlier host run. The two runs used different execution environments, so their absolute latency values should not be treated as a controlled before/after comparison. A host retry stopped during warmup with READINESS_UNAVAILABLE before the successful container run.

Within the successful container run, semantic and hybrid macro document recall@5 were 70.59% and 88.24%, with full recall on 12/17 and 15/17 supported questions. Unsupported questions with hits increased from 4/9 to 5/9. Median latency was 2.147 seconds semantic and 2.055 seconds hybrid, with p95 at 3.247 and 3.034 seconds. The hybrid pass incurred 78 additional fetch read units. Results are recorded in `evaluation/retrieval-hybrid-live-v2-results.json`.

The prior quality evaluation found an additional unsupported question receiving a hit when keyword records were preloaded. Hybrid retrieval should remain opt-in pending a larger labeled evaluation with exact identifiers. This review does not establish production retrieval quality.

## Hybrid retrieval benchmark - 16 November 2026

Keep `RETRIEVAL_HYBRID_ENABLED=false`. Compared the normal 0.25-cutoff semantic route and the configured hybrid route through authenticated FastAPI requests, using live Pinecone and an isolated embedding worker. The 26 labeled questions combine the 20 v3 cases with four literal phrase controls and two overlapping unsupported questions. Both modes used the same three existing synthetic documents (one active chunk each), with the order alternated after warmup. The fixture contents do not support a live exact-identifier comparison.

Among 25 successful pairs, macro document recall@5 rose from 68.8% to 75.0%, and full-recall questions rose from 11/16 to 12/16. Both modes returned hits on 4/9 unsupported questions and returned 10 passages from off-label documents. Paired p50 was 4.70 s semantic and 8.64 s hybrid, with a median within-pair increase of 3.80 s. Paired p95 was 6.75 s and 9.56 s. The unmatched semantic request returned HTTP 503 with PROVIDER_BUSY. The hybrid pass hit its deadline on 23/26 requests and observed 75 extra fetch read units across the measured run, including fetches that returned after the application deadline. The hybrid help-desk hours result recovered one expected source.

A separate ranking diagnostic preloaded the three active Pinecone records and reused the same semantic candidates within each pair. All 26 pairs completed. Macro document recall@5 increased from 70.6% to 88.2%, and full recall rose from 12/17 to 15/17. Unsupported questions with hits rose from 4/9 to 5/9, and off-label passages increased from 11 to 12. The ranking diagnostic bypassed the live fetch deadline and does not provide production latency measurements. Next: address the fetch deadline failures, then rerun the live comparison with identifier cases and a larger corpus.

Per-question results are in `evaluation/retrieval-hybrid-live-v1-results.json` and `evaluation/retrieval-hybrid-ranking-v1-results.json`. From `backend`, the runners are `python -m scripts.hybrid_retrieval_benchmark` and `python -m scripts.hybrid_retrieval_ranking_probe`, each with `--dataset ../evaluation/retrieval-fallback-v3.json --output <path>`. Use the local dev provider configuration and a dedicated embedding worker/Redis database. The evaluation used read-only Pinecone operations and disposable authentication state. No retrieval behavior or default was changed.

## Hybrid retrieval prototype - 16 November 2026

Implemented an opt-in keyword leg on codex/hybrid-retrieval from dev. RETRIEVAL_HYBRID_ENABLED defaults to false. The keyword leg fetches existing Pinecone records by deterministic active-version IDs using the same authorized document scope as semantic search. It matches contiguous normalized phrases and exact mixed letter/digit identifiers and combines the two rankings with reciprocal rank fusion. Document Search and Chat share the retrieval path. Existing reranking experiments remain isolated. Final actor, document visibility, and active-version checks still apply.

The keyword pass has a 64-chunk scope cap and a four-second deadline. Larger scopes, unusable chunk counts, or optional fetch failures use existing semantic results. This is a bounded prototype and requires no re-ingestion. Next: compare semantic and hybrid on labeled synthetic questions for exact terms, supported paraphrases, and unrelated questions. Measure recall, irrelevant matches, latency, and fetch cost before considering a dedicated lexical index or changing the default.

Fresh verification: 25 focused tests passed against local MongoDB and Redis with provider doubles, including semantic-miss recovery, timeout fallback, active-ID fetch limits, and share revocation and reindexing during keyword retrieval. Backend Ruff and diff checks passed. Live Pinecone retrieval and quality evaluation are pending. Local changes are ready for review.

## GitHub Actions CI - 13 November 2026

Added .github/workflows/ci.yml for pull requests to dev/main, pushes to dev, and manual runs. The backend job installs locked Python 3.12 dependencies, runs Ruff, and runs pytest with disposable MongoDB replica set, Redis, and Azurite services. The frontend job uses Node 22 and npm ci, then runs ESLint, TypeScript and a production build. CI uses no live Gemini or Pinecone credentials.

Pre-push review pinned Setup Node to the verified v6 commit. Fresh Linux Node 22 verification passed npm ci, ESLint, TypeScript, and the exact Turbopack production build command. Backend lint and lockfile checks passed. The clean committed backend snapshot passed 170 tests in 375.93 seconds with MongoDB, Redis, and the Azurite transport test enabled. GitHub-hosted verification is pending the push. Confirm both jobs before requiring the checks for merges.

## Retrieval experiment review checkpoint

Reviewed the opt-in reranking and fallback paths. The default search and chat calls omit the reranker. The new gating regression verifies that a candidate at the 0.25 baseline cutoff bypasses the model and that a broader candidate can be scored when there are no baseline candidates. The authenticated integration regression verifies that the default path rejects a 0.22 candidate and that a private share revocation and an index version change during reranking prevent either candidate from being returned.

Fresh verification: all 15 retrieval tests passed using local MongoDB and Redis. Backend Ruff and git diff checks passed. The prior 40-request live evaluation remains the quality evidence for the fallback, which matched baseline outcomes on all 20 questions. The vacation sign-off miss remains open. Keep the fallback experimental.

## Retrieval reranking fallback experiment

Added an opt-in fallback that preserves candidates at the 0.25 baseline cutoff and scores 0.20 candidates when there are no baseline candidates. Tested a minimum reranker logit of -4 on the original 12 cases and eight additional labeled questions against the same three synthetic documents. All 40 requests returned HTTP 200. Neither variant changed the other variant's result status or count. Both found all expected sources for 8/9 original and 2/4 new supported questions. Both returned no matches for 3/3 original and 1/4 new unsupported questions. Training reimbursement was preserved and vacation sign-off was still missed. The related unsupported cases that already matched the baseline bypassed the fallback. Targeted Ruff and git diff checks passed. The fallback remains evaluation-only. See evaluation/retrieval-fallback-v3-results.json.

## Retrieval reranking experiment

Added an opt-in live evaluation runner using the pinned MS MARCO MiniLM cross-encoder on CPU. It compares the 0.25 semantic baseline with 0.20 candidate retrieval and a minimum reranker logit of 0. The normal retrieval path continues to use its configured cutoff. The experiment applies reranking to validated candidates, before the existing final authorization and version checks.

All 24 live requests succeeded. The baseline found expected sources on 8/9 supported cases and returned no matches on 3/3 unsupported cases. The reranker found expected sources on 7/9 supported cases and returned no matches on 3/3 unsupported cases. It rejected the airfare false positive from the earlier broad-only run but also filtered out training reimbursement and vacation sign-off. Do not enable this configuration based on these results. A follow-up can preserve the existing high-confidence results and validate a rescue threshold for broader candidates on additional labeled examples. See evaluation/retrieval-rerank-v2-results.json for per-case outcomes and timings. Fourteen retrieval regression tests and targeted Ruff/diff checks passed.

## Dashboard checkpoint and retrieval baseline - 11 November 2026

Reviewed the dashboard, feedback, and Compose startup change on dev. The updated container reached Next.js Ready and the dashboard route returned HTTP 200. Prior rendered browser verification with synthetic data covered chart/table toggles, activity windows, refresh, mobile layout and API failure. The user confirmed live Admin acceptance on 11 November and previously confirmed feedback browser acceptance. The user authorized committing and pushing the dashboard, feedback, and Compose startup checkpoint to dev.

The checkpoint review added no-store caching headers to feedback responses, which can contain user comments. The running frontend container returned HTTP 200 for /admin and Compose configuration validation passed. Nine dashboard/chat integration tests passed, and the focused feedback rerun verified the cache header. Ruff, frontend ESLint, TypeScript, and diff checks passed. The separate retrieval baseline JSON files remain local for the next retrieval experiment.

Live retrieval on the three synthetic fixtures used the same 12 questions at cutoffs 0.25 and 0.20. At 0.25 the expected sources were found for 8/9 supported questions and all 3 unsupported questions returned no matches. At 0.20 recall improved to 9/9 and the unsupported airfare question returned an irrelevant passage. The first baseline batch hit the configured request limit for its final two cases. Those cases were rerun successfully in a separate batch. Results and questions are in evaluation/retrieval-baseline-v2-results.json and evaluation/retrieval-baseline-v2.json. The lower cutoff was evaluation-only. Next experiment: broader candidate retrieval followed by relevance reranking and rejection, compared against this baseline.

## Admin dashboard visual redesign - 10 November 2026

The dashboard now shows key metric cards, horizontal comparisons for chat, feedback, document statuses and user roles, a charts/tables toggle, 7/30-day controls, health badges and admin shortcuts. The redesign uses the existing dashboard API.

Fresh frontend ESLint, TypeScript and isolated production build passed. A rendered headless Edge check using synthetic authenticated API responses passed charts, tables, date-window requests, refresh, mobile-width overflow and unavailable/unknown states when the API cannot be reached. Docker's Linux engine was stopped during verification. Live Admin Chrome acceptance on localhost:3000 is pending. Changes remain uncommitted on dev.

## Admin dashboard and feedback review - 5 November 2026

User confirmed all feedback browser checks passed. Implemented the approved Admin-only dashboard with current document/user counts, 7/30-day ingestion/chat/feedback counts and separate API/embedding worker health. The dashboard exposes aggregates only. Failed metric queries show Unavailable, and a missing worker heartbeat does not affect counts. Usage reflects retained chat records. Feedback windows use first rating time.

Reviewed both changes and added a 16 KiB feedback request-body limit before JSON parsing. Nine chat/dashboard integration tests passed against isolated MongoDB and Redis, plus the focused feedback rerun after adding the body cap. Ruff, frontend lint and TypeScript passed. Dashboard API route is present in the running API. Worker states were exercised with controlled doubles. Dashboard Chrome acceptance remains pending. Changes are uncommitted on dev.

## Answer feedback checkpoint - 5 November 2026

User confirmed personal Chrome acceptance for chat and history, including the expected interrupted state after Stop and reload. Feedback is implemented on dev and awaits user browser acceptance. Completed visible answers show one-click Helpful and Unhelpful controls. Unhelpful opens an optional comment. Switching to Helpful clears the comment. FastAPI checks ownership and current source access. A unique MongoDB index enforces one rating per answer per user. History restores feedback and conversation deletion removes it. Feedback makes no model calls.

## History review fixes and dev checkpoint � 4 November 2026

C1/P1 and C2/P2 from the AI Chat review are fixed. History performs its conversation-deletion guard, revalidates the actor, then reads the entire page's source ACLs/active versions in one final batch; affected answers and citations are hidden with no further asynchronous work before returning. This is a final-read authorization boundary, not a claim that permissions cannot change after a database read or response transmission.

The UI retains the oldest loaded turn boundary, re-fetches every loaded page on focus/15-second refresh, and replaces the refreshed range together. Older answers receive current access checks rather than being retained from a stale cache. Request sequencing rejects superseded results; cursor pages deduplicate overlapping turns.

Fresh verification: seven chat integration tests passed in 26.49 seconds, including two new real-Mongo/Redis page-assembly regressions (source deletion and index-version change after individual turn serialization). Rendered headless Edge chat regression passed, including 40-message focus/timer retention, revoked older-answer hiding, all 45 messages reachable without duplicates, and existing send/citation/quota/Stop/history/CRUD checks. Full backend Ruff (no-cache), frontend ESLint/TypeScript and diff checks passed. No fresh live Gemini/Pinecone calls, model download, or production build. Fresh full backend suite: 166 passed, 1 skipped in 252.55 seconds (the optional Azurite transport check was not enabled).

User authorized committing and pushing the pending reviewed AI Chat/retrieval hardening checkpoint and these fixes to dev. Main stays unchanged; private environment files remain ignored. The candidate-file credential pattern scan found only the known public Azurite emulator key. This entry precedes Git operations; verify the resulting commit/remote hash with Git. Next: user acceptance of chat/history, then the next agreed feature. Historical open C1/C2 notes below are superseded by this entry.

## Current uncommitted code review — 4 November 2026

Reviewed retrieval hardening and AI Chat against a65d19c. Two open findings: P1 saved-answer history responses can retain an early private answer if its source is revoked while later turns are serialized; P2 automatic 15-second/focus refresh replaces the newest page and discards older loaded history. C1 was reproduced with actual handler/helper logic and source-state doubles; C2 follows directly from the inspected refresh/pagination state updates. Details and fix guidance: [AI Chat review](docs/review-ai-chat-2026-11-04.md).

Fresh verification: 47 distinct focused chat/retrieval-hardening checks passed (40 initial passes plus all 7 service-backed checks passing after restoring stopped MongoDB/Redis; the seven-check rerun took 26.23 seconds). Full backend Ruff, frontend ESLint/TypeScript and diff checks passed. Only local database/cache test dependencies were started; no application worker/model or live AI/vector provider calls. No new full-suite, browser or production-build pass is claimed. Application source is unchanged by this review; the report/tracker update remains uncommitted. No commit or push made.

Next: fix/regression-test the history disclosure race and preserve loaded message pages during access-refresh polling, then review the resulting diff and checkpoint dev when authorized. Earlier implementation and synthetic calibration records below do not close these findings.

## AI Chat checkpoint — 3 November 2026

User authorized completing the six AI Chat blocks, superseding the earlier generation hold. Gemini is selected; development default changed explicitly from gemini-3.8-flash (live HTTP 503) to gemini-3.6-flash (actual API-container stream passed in 4.395s). Implemented owner-only conversation CRUD/history, authenticated POST SSE, authorized retrieval + follow-up rewrite, bounded context/provenance, validated source IDs, saved-answer access/version hiding, transactional fenced leases, idempotent replay/conflict checks, Stop/disconnect recovery, rate/capacity/body limits and chat/history UI. No new dependencies, paid resources, commit or push; preserve all earlier retrieval fixes on dev.

Fresh full backend suite: 165 passed in 306.28s using isolated MongoDB/Redis and synthetic Azurite. Initial six host-Mongo test failures were corrected by using directConnection=true in the host test URL. Frontend lint/TypeScript, isolated production build and synthetic rendered chat regression passed. Live real MiniLM/Celery/Pinecone/Gemini initial + follow-up RAG questions both completed HTTP 200 with validated sources and the existing synthetic policy's 20-day answer. Only disposable auth/chat metadata was written; originals/ACLs/vectors unchanged. Local services started; core readiness 200 and in-container chat page 200 after startup compilation.

The 30-case synthetic generation quality gate completed on 4 November and passed its development-calibration target. All 20 supported answers passed citation integrity, expected-term checks and manual evidence/claim-support review. All ten insufficient-evidence cases returned the exact approved abstention, with zero citations and no unsupported claims. Earlier attempts encountered three first-content timeouts while a separate provider probe took 28.156 seconds against the unchanged 30-second application deadline; a later normal resume completed all remaining cases without relaxing production limits. This is a small synthetic calibration result, not production accuracy, latency, adversarial or load evidence. See `evaluation/chat/results-2026-11-03.json`. Main remains unchanged, with no commit or push; code review and checkpointing are next.

## Current retrieval fixes checkpoint — 2 November 2026

R1-R5 are fixed in the dev working tree against a65d19c; no commit or push made. Provider calls retain process-wide capacity until underlying synchronous work actually ends; reprocessing keeps pending counts on jobs and atomically publishes active version/count; model warm-up/validation happens in the Celery prefork execution child with cache-only loading and explicit provisioning; a separate authenticated bounded retrieval-readiness resource gates search; UI preserves structured errors, request IDs, Retry-After and separate cancellation/timeout states. See docs/retrieval-review-fixes-2026-11-02.md for implementation and exact limits. Earlier open-finding/E2 notes below are historical and superseded by this checkpoint.

Verification: full backend suite 126 passed in 347.25 seconds. Final focused suite 14 passed in 23.31 seconds after adding the readiness-deadline regression and tuning the bounded vector probe from 3 to 8 seconds (remote index/stats measured 2.97 seconds without overhead). Ruff, final frontend full lint/TypeScript, isolated production build, search UI headless regression and dependency lock checks passed. The existing headless pagination regression also passed: document row 101 and older failed jobs remain reachable, polling preserves the selected page, and filters reset pagination without browser errors. A network-disabled temporary container with read-only cache/code produced 384-dimensional embeddings and rejected a missing cache explicitly (86.83 seconds combined). No query triggered the live worker's model initialization.

Live worker-stop test passed: retrieval reported WORKER_UNAVAILABLE while core API/MongoDB/Redis/Azurite health stayed ready; worker was restored in finally, transitioned warming -> model/vector ready, and an authorized synthetic search returned MATCHES after recovery. Full synthetic live fixture evaluation also passed all 8 requests (expected source-document recall on 6 positives, 2 unrelated queries empty); provider outage/deadline interleavings are tested with doubles. Original documents/ACLs/Pinecone vectors were not modified. User previously confirmed E2 recovery as well. Cold startup is still CPU/resource dependent; the fix is truthful readiness/admission and no implicit downloads, not an instant-start guarantee.

Next: review this fixes diff, then commit/push dev when authorized. Do not start generation until the corrections are accepted. Main remains unchanged. No new dependencies, cloud resources, credentials or paid model calls were introduced.

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
