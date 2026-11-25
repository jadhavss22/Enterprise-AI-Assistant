# Reranking evaluation decision - 25 November 2026

Deployment decision: keep reranking disabled by default pending representative acceptance and resolution of failed gates. Hybrid remains disabled under its independent decision. Query rewriting was not evaluated or enabled. Synthetic profile eligibility is recorded separately below; it is not production enablement.

- semantic_reranked: keep_disabled. Failed gates: contention_embedding_p95_within_20_percent_plus_250ms. Per-question recall losses: none.
- hybrid_reranked: keep_disabled. Failed gates: contention_embedding_p95_within_20_percent_plus_250ms. Per-question recall losses: none.

## Measured comparison

Used dev baseline d0341bc with the unchanged pre-labeled 96-question corpus over 12 synthetic one-chunk policies: 48 supported and 48 unsupported questions. All 384 measured requests returned HTTP 200. Results are one warmed sequential round with rotated profile order, actual execution-worker embeddings, actual pinned cross-encoder inference, and actual Pinecone. The semantic cutoff remained 0.25, candidate limit 20, and final result limit 5. Ranking metrics average over supported questions only. The same worker held both warmed models for all profiles.

| Configuration | MRR | nDCG@5 | Recall@5 | Irrelevant passages | Unsupported hits | p50 / p95 latency | Model completions / fallbacks |
|---|---:|---:|---:|---:|---:|---:|---:|
| semantic | 0.7917 | 0.8234 | 91.67% | 191 | 35/48 | 3.153 s / 4.356 s | 0 / 0 |
| hybrid | 0.9375 | 0.9484 | 97.92% | 195 | 35/48 | 4.024 s / 6.251 s | 0 / 0 |
| semantic_reranked | 0.9062 | 0.9090 | 91.67% | 191 | 35/48 | 3.439 s / 4.251 s | 60 / 0 |
| hybrid_reranked | 0.9583 | 0.9638 | 97.92% | 195 | 35/48 | 4.201 s / 5.781 s | 63 / 0 |


| Supported category | Configuration | MRR | nDCG@5 | Recall@5 |
|---|---|---:|---:|---:|
| identifier | semantic | 0.5417 | 0.5942 | 75.00% |
| identifier | hybrid | 0.9167 | 0.9385 | 100.00% |
| identifier | semantic_reranked | 0.7500 | 0.7500 | 75.00% |
| identifier | hybrid_reranked | 0.9583 | 0.9692 | 100.00% |
| phrase | semantic | 0.7500 | 0.8135 | 100.00% |
| phrase | hybrid | 0.9583 | 0.9692 | 100.00% |
| phrase | semantic_reranked | 1.0000 | 1.0000 | 100.00% |
| phrase | hybrid_reranked | 1.0000 | 1.0000 | 100.00% |
| paraphrase | semantic | 0.9375 | 0.9430 | 95.83% |
| paraphrase | hybrid | 0.9375 | 0.9430 | 95.83% |
| paraphrase | semantic_reranked | 0.9375 | 0.9430 | 95.83% |
| paraphrase | hybrid_reranked | 0.9375 | 0.9430 | 95.83% |


Reranking only reorders approved candidates; no logit threshold or cutoff change was introduced. It cannot recover absent input candidates or independently abstain on unsupported questions. Relative ranking gains therefore do not establish answer correctness or acceptable production precision. Individual recall losses are recorded above even when an aggregate ranking metric improves.

Both reranked profiles increased median API latency in this round. Their lower observed p95 values are not a speedup claim: the network-backed samples come from one warmed round and need repeated timing trials.

## Worker contention

| Phase | Job kind | Jobs / invalid | Queue wait p50 / p95 | Execution p95 | Publish-to-completion p95 |
|---|---|---:|---:|---:|---:|
| isolated | embedding | 12 / 0 | 7.95 / 48.29 ms | 54.20 ms | 88.39 ms |
| isolated | rerank | 12 / 0 | 5.87 / 9.97 ms | 644.88 ms | 649.04 ms |
| mixed | embedding | 6 / 0 | 671.19 / 1761.84 ms | 67.66 ms | 1829.50 ms |
| mixed | rerank | 18 / 0 | 514.55 / 1124.67 ms | 624.00 ms | 1748.67 ms |


Contention gate passed: False. The gate compares mixed embedding p95 publish-to-worker-completion time with isolated embedding p95 times 1.2 plus 250 ms. There were 12 isolated jobs per kind and six mixed waves with three maximum-candidate-count reranks plus one embedding, alternating submission order. This is a controlled burst-sensitivity probe on one prefork execution process with one CPU math thread, not a sustained load test or an API-capacity prediction. Worker timestamps exclude result-delivery polling; API latencies above include actual request round trips.

The next configuration trial should isolate reranking execution from query embeddings and repeat the same quality and contention checks before representative acceptance. This evaluation has not implemented or tested that deployment change. Retain the existing timeouts and disabled defaults meanwhile.

## Configuration, verification, and limits

The provisioned model is cross-encoder/ms-marco-MiniLM-L6-v2 at pinned revision ce0834f22110de6d9222af7a7a03628121708969, using CPU, safetensors, remote code disabled, max length 384 tokens, batch size 8, and cache-only startup/inference. Reranking has the existing eight-second optional deadline and input limits. Worker concurrency is one (prefork), OMP_NUM_THREADS=1, MKL_NUM_THREADS=1, on the isolated rerank-evaluation queue. Both task types share that process. See docs/reranking-evaluation.md for provisioning and reproduction commands.

All 48 focused evaluation, reranker, and lifecycle tests passed, along with backend Ruff and focused formatting. An offline audit matched the canonical dataset fingerprint and labels, recalculated every MRR/nDCG observation, checked candidate-set preservation, and recalculated summaries, gates, decisions, and contention summaries. Raw observations are in evaluation/retrieval-rerank-expanded-v1-results.json. The runner refreshed its synthetic session 2 time(s) outside measured search latency. Generated namespaces, test database, and rate-limit state were cleaned; the pinned model cache intentionally persists. No production document content was used.

These results do not establish representative organization quality, long-passage behavior, cold-start latency, or sustained ingestion/search contention. The 12 synthetic policy families contain correlated questions and short passages; use independent organization labels, repeated timings, and realistic mixed workloads before enabling reranking. The existing account, ACL, active-version, and optional fallback behavior remains in force.

## Code review follow-up

Review fixed acceptance reporting for HTTP 200 provider fallbacks and datasets without supported ranking observations. Provider errors in either compared profile now block acceptance, while missing ranking observations keep the experiment disabled. The raw live observations and measured outcomes are unchanged; derived provider-error gates were recalculated and pass for this saved run. The contention gate still fails for both reranked profiles.

Fresh review verification passed 57 focused tests; two service-backed integration cases were skipped locally pending the required CI environment. Backend lint, focused formatting, CLI help, whitespace checks, and an independent offline audit of labels, coverage, metrics, candidate preservation, all diagnostic jobs, summaries, and decisions passed. No live benchmark or model provisioning was repeated during review. Publication proceeds through a PR into protected dev and both required CI checks.
