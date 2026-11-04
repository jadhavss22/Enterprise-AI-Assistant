# AI Chat implementation checkpoint — 3 November 2026

## Decision and actual scope

User approved Gemini Developer API in place of Groq. Development default is
`gemini-3.6-flash`: a real API-container streaming probe succeeded in 4.395 seconds
(first content at 4.391 seconds). The initially proposed `gemini-3.8-flash` returned
HTTP 503 / InternalServerError in the API runtime. This is an explicit model
decision, not an automatic cross-model/provider fallback. The existing private
key was preserved. No billing upgrade, cloud resource or dependency was added.

Model/pricing references checked during selection:
- https://ai.google.dev/gemini-api/docs/openai
- https://ai.google.dev/gemini-api/docs/pricing

MiniLM CPU embeddings and the existing Pinecone 384-dimensional index are
unchanged; re-upload/reindex is unnecessary. Only synthetic evidence was sent
in automated live generation tests. Free API quotas remain operational limits.
Provider data terms must be reviewed before using real enterprise information.

## API contracts

All routes require the current active user/session. Conversation ownership is
enforced even for Admin: Admin cannot read another user's chat.

| Method | Path under /api/v1 | Contract |
| --- | --- | --- |
| POST | /conversations | `{title}`; 201, conversation metadata |
| GET | /conversations | `limit` (1–50), optional cursor; owner list, next_cursor |
| PATCH | /conversations/{id} | `{title}` (1–100 characters) |
| DELETE | /conversations/{id} | Soft-delete guard and transactionally delete messages; 204 |
| GET | /conversations/{id}/messages | Paginated turns, newest page in chronological order |
| POST | /conversations/{id}/messages/stream | `{question, client_request_id}`; UUID idempotency key; authenticated POST SSE |
| POST | /conversations/{id}/generations/{request_id}/cancel | Owner-only idempotent cancellation; 204 |

SSE events: started, token, citations, completed, error. Tokens are provisional;
verified references appear only at successful completion. JSON HTTP failures
occur before streaming; failures after headers are structured SSE errors with
safe codes, server request IDs and available retry delays. History reload is
authoritative if the connection was lost after durable completion.

## Security and lifecycle

- Reuse current backend retrieval authorization, active version and session checks.
- Recheck all evidence and history provenance before model requests, before each
  emitted content delta and at final source display. The system cannot retract
  text legitimately delivered before a later permission change.
- Saved answers are hidden when ANY document/version used as evidence/history
  is inaccessible or changed. Incomplete outputs are never replayed as complete
  or included in later prompt history.
- Up to three earlier complete turns (six messages); conservative UTF-8 byte
  accounting plus role overhead bounds history to 2,000 units without downloading
  a tokenizer at API startup. This intentionally underuses the approximate token
  budget. Current retrieval still rejects queries over its model input limit.
- Query rewrite only resolves follow-up references; every rewritten search is
  independently authorized. Source text/history are untrusted prompt data.
- Prompt maximum 48,000 characters, final output maximum 16,000 characters,
  completion token budget 2,048 (includes provider reasoning), low reasoning effort.
- One generation per conversation: MongoDB transactions, monotonically increasing
  fence, 30-second lease and five-second renewal. Expired leases cannot renew or
  write. New attempts recover interrupted rows. Runtime pipeline deadline 180s;
  first provider content deadline 30s; provider total 90s; browser total 195s.
- Two active HTTP generations per API process; six generation requests/user/minute.
  Per-process capacity is not a deployment-wide concurrency quota.
- Stable request UUID plus question hash prevents duplicates and conflicting reuse.
  Completed repeats replay after source checks; ended attempts require a new UUID.
- Stop/disconnect closes the provider iterator, preserves incomplete state, and
  conditionally releases only the matching lease. Completion may win a Stop race.
- Provenance is a deduplicated union capped at 100 source/version identities;
  recursive history must not cause exponentially growing MongoDB rows.
- At most 500 turns/conversation; list/message pagination remains available.
- Titles are user-entered; prompts/answers/document contents are not audit logs.
  Generation end audits are transactionally persisted with guarded final writes.
- API keys remain backend-only; no token persistence, automatic paid fallback,
  browser tools, external search, agents, Next.js API backend or TLS bypass.

## Files and dependencies

Backend: app/chat/{provider,grounding,schemas,storage,service,routes}.py,
configuration, main router/storage registration, no-store response handling and
16 KiB bounded JSON bodies. Reuses installed OpenAI-compatible client, httpx,
Motor/PyMongo, Redis, FastAPI and Pydantic; lockfiles unchanged.

Frontend: chat/history routes, chat-workspace.tsx, authenticated stream method
in auth-context.tsx, incremental UTF-8/SSE parser in chat-stream.ts, responsive CSS.
Source downloads reuse the authenticated backend path; answer text is rendered
as plain text rather than executable model-generated HTML.

Tests: test_chat_provider.py, test_chat_grounding.py, test_chat_integration.py,
frontend/tests/chat-smoke.cjs. Live probes: verify_chat_provider.py,
chat_live_check.py, evaluate_chat.py. Synthetic evaluation: evaluation/chat/.

## Verification and limits

- Focused chat regression: 37 passed before the final provenance-union regression.
- Full final backend suite: 165 passed in 306.28 seconds, with real isolated MongoDB
  replica-set/Redis and synthetic local Azurite transport enabled. Provider calls
  in unit/integration regressions are explicit doubles, not live model quality tests.
- An earlier full run had six failures because the host test URL omitted
  directConnection=true and workers followed Docker's internal mongo hostname.
  Correcting the test environment resolved them without ingestion-code changes.
- Frontend lint, TypeScript and isolated webpack production build passed. Rendered
  browser regression passed create/send/citations, revoked-source hiding, quota
  countdown/request ID, truncated streams, Stop, rename/history/delete, no browser
  errors and empty local/session storage. API responses in this browser test are
  synthetic. Real backend/provider integration is verified separately below.
- Live API ASGI smoke with disposable auth/chat DB used actual Celery/MiniLM,
  Pinecone and Gemini. Initial and follow-up questions both completed HTTP 200,
  one source each, correct 20-day synthetic uploaded policy, no SSE errors. Original
  documents/ACLs/vectors were read-only; disposable test accounts/chats were removed.
- API core readiness returned 200. API container served /chat with HTTP 200 after
  npm installation and cold Next.js compilation. Initial frontend startup requests
  timed out while compilation was incomplete; production build was isolated.
- Generation-only quality calibration completed on 4 November: all 20 supported
  answers passed citation integrity, expected-term checks and manual evidence/claim
  support review. All ten insufficient-evidence cases returned the exact approved
  abstention with zero citations and no unsupported claims. Earlier attempts had
  three first-content timeouts while a separate provider probe took 28.156 seconds,
  close to the unchanged 30-second application deadline; a later normal resume
  completed all remaining cases. Production timeouts were not relaxed. This passes
  the small development-calibration target, not production accuracy, latency,
  adversarial or load acceptance.

Acceptance target: all deterministic security/lifecycle tests pass; at least 18/20
supported answers correct and supported by evidence, all ten insufficient-evidence
cases abstain; valid citations must ALSO support the claims. These are small
development calibration targets, not production accuracy or latency guarantees.

## Commands (PowerShell, repository root)

```powershell
docker compose up -d
docker compose exec -T api python scripts/verify_chat_provider.py
docker compose exec -T api python -m scripts.chat_live_check
.\backend\.venv\Scripts\python.exe backend/scripts/evaluate_chat.py --live --resume --output evaluation/chat/results-2026-11-03.json
```

Host-backed tests must use
`AUTH_TEST_MONGODB_URL=mongodb://localhost:27017/?replicaSet=rs0&directConnection=true`,
`AUTH_TEST_REDIS_URL=redis://localhost:6379/0` and `RUN_LOCAL_AZURITE_TEST=1`.
The opt-in live RAG runner requires the three named synthetic fixtures to already
be ingested. The evaluator runs on the host because the root evaluation folder
is not mounted into the API container. It stops on a provider/quota error and
can resume completed case IDs with at least 15 seconds between new calls.

No commit or push. Work remains on dev; main is unchanged. Retrieval fixes are
still part of the pre-existing uncommitted working tree. Production deployment,
feedback, dashboards, broader adversarial/load evaluation and cloud hardening
are separate milestones.
