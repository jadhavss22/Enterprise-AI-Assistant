# Hybrid retrieval enablement evaluation

The expanded comparison uses 96 questions labeled before execution over 12 synthetic policy documents. Forty-eight questions are supported: 12 exact identifiers, 12 literal phrases, and 24 direct or indirect paraphrases. Forty-eight are unsupported: 12 near-topic questions requesting absent rules, 24 missing or mismatched identifier questions, and 12 unrelated or absent-topic questions. Similar identifiers such as HR-42/HR-420 and IT-105/IT-1050 deliberately create distractors. Each supported label includes a source and verbatim evidence; each negative includes a rationale. Review labels before adding real organization data.

Each policy occupies one bounded chunk. A returned passage is relevant only if its source is labeled relevant; because every source has one chunk, this is also passage relevance. A document that merely repeats the topic or identifier does not answer an unsupported question. This evaluates candidate retrieval, not generated answer correctness or ingestion quality.

## Run

Start the configured embedding execution worker with its provisioned model cache and local MongoDB/Redis. Use the same configured embedding model and Pinecone index as retrieval. The runner does not download models or modify existing documents. It requires explicit synthetic-write opt-in because it creates fresh ObjectId-based namespaces and then deletes those exact namespaces.

```sh
python -m scripts.hybrid_expanded_benchmark \
  --dataset tests/fixtures/retrieval-hybrid-expanded-v1.json \
  --output /evaluation/retrieval-hybrid-expanded-v1-results.json \
  --rounds 1 --allow-synthetic-writes
```

Run from the backend directory. The example output path assumes the evaluation directory is mounted at /evaluation in the API container. The canonical labels live at backend/tests/fixtures/retrieval-hybrid-expanded-v1.json so they are included in both CI checkout and the backend test image. The runner creates a disposable test_hybrid_expanded_* database and generated synthetic user, not accounts in the application database. It uses the authenticated FastAPI search endpoint with actual Celery embeddings and actual Pinecone namespace queries/fetches. Source embeddings use the same worker. Reranking and query rewriting are explicitly disabled. Production feature flags and semantic cutoff are not changed; the configured cutoff is recorded in the output.

The output is written atomically after every completed pair and includes a UTF-8/LF-normalized dataset SHA-256, settings, timestamps, disposable resource IDs, warmups, per-question results, summary/category metrics, regressions, and cleanup status. After interruption, inspect the resource manifest and remove only its listed synthetic namespaces, test database, and matching rate-limit keys. Do not run broad deletion commands. A nonzero runner exit or incomplete/failed cleanup blocks acceptance. Cancellation waits for any admitted synchronous vector upsert to finish before deleting the generated namespaces, preventing a late write from recreating cleaned resources. The journal records the configured index name for future runs.

The runner refuses to overwrite the input labels or a journal with unresolved cleanup. Atomic saves use an exclusively created temporary file rather than a predictable filename. A per-output .lock file prevents concurrent runs from replacing each other's resource manifest. If the process is forcibly stopped, inspect and clean its journal before removing that exact stale lock. Long runs refresh the synthetic session before access-token expiry using the existing cookie, Origin, and CSRF endpoints. Refresh happens before a timed search, and the output records the number of refreshes. A failed refresh stops the benchmark and preserves partial observations instead of silently retrying authentication failures inside the timed search.

## Measurement and gates

Semantic and hybrid run sequentially with alternating first mode; additional rounds reverse the order again. No semantic results, query vectors, or keyword records are replayed between modes. One warmup per mode is excluded. Latency measures the in-process authenticated API path, including worker/provider work, excluding browser and external API transport. It is a single-user comparison, not a load test.

Document recall@5 is averaged over all supported questions. Failed requests count as zero recall and remain in the latency/error counts. The report includes irrelevant passage counts, passage precision, unsupported-question hit rate, median and nearest-rank p95 latency, semantic and keyword timeout/error counts, fetch calls, requested/returned records, and provider-reported fetch read units with coverage. Missing usage remains unknown rather than zero. Fetch read units are not a monetary estimate or total query cost. Pending synchronous fetch work can outlive its optional deadline, so requested/returned records and error counts must be read together.

The declared gates require successful requests, an exercised keyword pass, improved macro recall with no individual recall losses, no newly introduced unsupported matches, no increase in irrelevant passages, no keyword errors/timeouts, no semantic timeout increase, and hybrid p95 no greater than semantic p95 times 1.2 plus 250 ms. Passing this synthetic comparison permits representative acceptance testing; it does not automatically enable hybrid retrieval. A failed gate keeps RETRIEVAL_HYBRID_ENABLED=false.

The 64-chunk keyword cap and permission/version checks remain unchanged. This corpus is below that cap and cannot establish quality or latency for larger scopes. Existing regression tests cover over-budget fallback, keyword timeout fallback, permission revocation, and active-version changes. Representative company questions and documents, repeated latency trials, and concurrent-load testing remain separate rollout evidence.
