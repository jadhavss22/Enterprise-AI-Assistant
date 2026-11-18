## Hybrid retrieval review - 18 November 2026

Reviewed the opt-in retrieval path, provider deadlines and cancellation, authorization and active-version rechecks, keyword limits, fusion, and evaluation runners against dev.

Two runtime findings were corrected. The keyword pass constructed a second Pinecone client and resolved its host inside the four-second deadline. Five read-only timing samples on the synthetic fixtures measured client setup at 2.88–4.65 seconds, with fresh fetches around 1.0–1.13 seconds. Retrieval now passes the semantic search's index client into the keyword pass.

Keyword fetches also shared the required provider pool. A synchronous fetch can continue after asyncio cancellation and retain its permit, potentially making later semantic requests return PROVIDER_BUSY. Optional keyword calls now use a separate bounded pool, retain their permits until synchronous completion, and pass the remaining overall deadline to each fetch.

The regression tests verify that the keyword pass receives the same index client and that a timed-out keyword call retains its own permit while the semantic provider pool remains available. Existing source authorization and active-version checks still run after both retrieval legs.

Fresh verification passed all 26 retrieval, keyword, hardening, fallback experiment, and Chat integration tests against the local MongoDB replica set and Redis in 161.34 seconds. Backend Ruff and focused format checks passed. Initial runs encountered stopped test services, which were restored before the passing run.

The corrected live benchmark ran inside the API container with the isolated embedding worker and three existing synthetic documents. All 52 measured searches returned HTTP 200. All 26 hybrid keyword passes completed without fetch errors, compared with 23 deadline failures in the earlier host run. The two runs used different execution environments, so their absolute latency values should not be treated as a controlled before/after comparison. A host retry stopped during warmup with READINESS_UNAVAILABLE before the successful container run.

Within the successful container run, semantic and hybrid macro document recall@5 were 70.59% and 88.24%, with full recall on 12/17 and 15/17 supported questions. Unsupported questions with hits increased from 4/9 to 5/9. Median latency was 2.147 seconds semantic and 2.055 seconds hybrid, with p95 at 3.247 and 3.034 seconds. The hybrid pass incurred 78 additional fetch read units. Results are recorded in `evaluation/retrieval-hybrid-live-v2-results.json`.

The prior quality evaluation found an additional unsupported question receiving a hit when keyword records were preloaded. Hybrid retrieval should remain opt-in pending a larger labeled evaluation with exact identifiers. This review does not establish production retrieval quality.
