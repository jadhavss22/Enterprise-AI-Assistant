# Review: uncommitted retrieval hardening and AI Chat

Reviewed 4 November 2026 against baseline `a65d19c` on `dev`. Scope includes the earlier retrieval hardening fixes and the new Gemini chat/backend/history/streaming UI, scripts, evaluation artifacts and documentation. This review does not implement fixes or authorize a release.

## Findings

### C1 — P1: History responses can return an answer revoked during page assembly

`backend/app/chat/routes.py:106-110` checks each turn through `public_turn` sequentially and then rechecks only conversation ownership. If a source is revoked/deleted/reindexed after an early turn's check while later turns are being serialized, the earlier answer and citations stay in the response. The final ownership check does not revalidate the page's source manifests. A focused probe using the actual messages handler and public_turn helper reproduced returning a complete private-source answer after source access was revoked between row serializations. This was a controlled source-state double, not a live-user permission change.

Revalidate the current actor and the page's source/version manifests as a batch at the final disclosure boundary, hide affected answers/citations, and avoid subsequent awaits that unnecessarily widen that window. Retain the final conversation-deletion guard before the final source read. Add a multi-turn regression that revokes a source while another turn is being serialized. Existing tests verify revocation before a history request and during generation, but not this history-page interleaving.

### C2 — P2: Automatic refresh removes already-loaded older messages

`frontend/src/components/chat-workspace.tsx:56-60,80-85` prepends older pages when a cursor is supplied, but the 15-second/focus refresh calls loadMessages without a cursor, replacing the entire loaded list with the newest page and resetting its cursor. After Load older messages, those older turns disappear on the next automatic refresh or window focus. Revalidation-triggered callback changes can also refresh the same way.

Keep the loaded range/page state during refresh. Refresh all visible pages or merge a refreshed range without dropping older turns; ensure revoked/hidden answers within the retained range are still refreshed and removed rather than leaving a stale sensitive-content cache. Test loading more than one page, advancing the refresh timer, and focusing the window. This finding is based on the inspected state updates and timer path; a fresh browser reproduction was not run in this review.

## Verification

- Fresh focused command covered `test_chat_grounding.py`, `test_chat_provider.py`, `test_chat_integration.py` and `test_retrieval_hardening.py`.
- Initially 40 checks passed and 7 service-backed setup checks errored because local MongoDB/Redis were stopped. The sandbox also denied Docker named-pipe access. An approved outside-sandbox check confirmed the environment issue; only MongoDB, Redis and replica-set initialization were started. No application worker/model/AI provider was started by the review.
- After services were ready, all seven affected integration checks passed in 26.23 seconds. Across the focused selection, **47 distinct checks passed**. The full 165-test suite recorded in the tracker is earlier evidence and was not rerun here.
- Full backend Ruff, frontend ESLint, TypeScript (`tsc --noEmit --incremental false`) and `git diff --check` passed.
- The C1 probe used actual route/service logic with controlled data doubles and no external calls; its helper is in the separate Documents workspace as `review_chat_cases.py`, not committed repository regression coverage.
- No live Gemini/Pinecone evaluation, new model download, new browser test or production build was performed. The existing 30-case generation calibration remains synthetic development evidence, not a production/adversarial quality guarantee.
- No application source, private environment settings, real uploaded documents, vectors or permissions were changed. The review record and tracker are the only intended repository edits. No commit or push was performed.

## Next steps

Fix C1 and add the interleaving regression before treating saved-answer access enforcement as complete. Fix C2 with refresh-aware pagination and a rendered browser regression. Re-run the relevant checks and update the tracker before committing this feature checkpoint on `dev` when authorized. Preserve the existing retrieval fixes; this review did not reproduce the earlier R1-R5 issues as still open.


## Resolution � 4 November 2026

C1 and C2 are fixed in the authorized checkpoint. The messages route now performs a final batch actor/source ACL/version check after serialization and the conversation guard, masking affected answers/citations with no subsequent awaits. New integration regressions change/delete the source after both individual turn checks and assert both answers are hidden.

Refresh now re-fetches the complete loaded range through its oldest turn, publishes it together, and rejects superseded requests. The rendered regression opens 45 turns, loads 40, revokes an older answer, verifies focus and timer refresh keep 40 questions while hiding the answer, then loads the final five without duplicates. Seven chat integration tests and the rendered chat regression passed; full lint/typecheck and diff checks passed. See the latest PROJECT_STATUS.md entry for full-suite evidence and checkpoint limits. Original findings above remain the historical review record.
