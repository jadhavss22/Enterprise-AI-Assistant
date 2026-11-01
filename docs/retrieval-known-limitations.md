# Retrieval operational limits and recovery notes

Recorded 1 November 2026. These are observed behavior and current development limits, not production guarantees.

## Confirmed observations

- With the Celery worker stopped, search returns a retryable busy/unavailable response. Ingestion also cannot progress until a worker is available. This is expected dependency-failure handling (manual case E1).
- After restarting the worker, the CPU model must reload into process memory. Earlier startup took about 100 seconds; the latest log recorded 118.59 seconds. The search embedding wait is 20 seconds, so early requests can fail while the worker warms. A running container or Celery pong does not establish model readiness. Persistent model files do not preserve a loaded model across process restarts.
- The latest worker startup attempted Hugging Face requests and logged DNS failures (Name or service not known). Cached-model startup still performs network lookups in the current implementation, which can add retries/delay.
- The second screenshot is a different failure: API logged PineconeConnectionError and returned HTTP 503. Later worker embedding tasks completed in about 0.04–0.05 seconds, so warm-up does not explain that later provider failure. Internet/DNS/proxy/container routing needs verification; the precise Pinecone connection cause was not isolated in this observation.
- End-to-end search currently requires a reachable Pinecone service, even though embeddings run locally. There is no local vector-search fallback. Provider errors must remain visibly distinct from no relevant results.

## Current limits to remember

| Operation/condition | Current behavior |
|---|---|
| Worker restart | Reloads model; search can time out until warm |
| Ingestion occupies the single worker | Query embeddings wait; search may return busy |
| Missing internet or provider connectivity | Pinecone search cannot complete; cached model startup may also be delayed by Hub lookups |
| Login/document listing/source download | Do not require the embedding worker, but still require their own API/database/storage dependencies |
| Over 10 searches per user/minute | 429 rate limit; retry after the indicated window |
| Over 25 authorized active document versions | Explicit scope-limit error; not a partial search |
| Over 2000 characters or model content-token limit (254 for current MiniLM) | Input rejected rather than silently truncated |
| Search cancellation | Stops browser waiting; it does not guarantee an already-running worker/provider operation is immediately terminated |

Stopping/restarting the worker does not itself delete original documents or Pinecone vectors. No data loss was established by these screenshots.

## Review actions (proposed, not implemented)

1. Warm the model deliberately at worker startup and expose model readiness separately from process/broker liveness.
2. Support validated cache-only/offline loading after the initial model download; keep an explicit download/configuration path for a missing cache. Do not disable TLS.
3. Separate offline/provider-unavailable, warming and queue-busy messages where reliably detectable; include safe request IDs and retry guidance.
4. Verify end-to-end recovery after restoring internet/container DNS, not merely after starting the worker.
5. Measure query/ingestion contention before deciding on separate queues/workers; the current 8 GB laptop constrains additional model copies.

## Acceptance status

User reports the remaining manual cases passed. E1 correctly displayed an unavailable response with the worker stopped. E2 end-to-end recovery is NOT passed from the supplied screenshots: the second screenshot still shows an error. A successful authorized passage response after connectivity recovery is needed to close it. Automated regression and prior live calibration results remain historical evidence in PROJECT_STATUS.md; they do not override this current recovery failure. No implementation, credential, permission, commit or push change was made by this observation.
