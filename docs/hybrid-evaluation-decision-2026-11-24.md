# Hybrid retrieval evaluation decision - 24 November 2026

Decision: keep hybrid retrieval disabled by default. The expanded synthetic comparison does not satisfy all declared gates. Failed gates: irrelevant_passages_do_not_increase, p95_within_20_percent_plus_250ms. Reranking and query rewriting remain separate evaluations.

## Evidence

Evaluated dev baseline 2b1c42b (OCR review merge) with actual local sentence-transformers/all-MiniLM-L6-v2 execution-worker embeddings and actual Pinecone queries/fetches. The labeled set has 96 questions over 12 one-chunk synthetic policies, with 48 supported and 48 unsupported questions. The semantic cutoff remained 0.25, candidate limit 20, and result limit 5. Neither reranking nor rewriting ran. Each pair alternated first mode. This was one warmed sequential round, not a load test or a representative company-data evaluation.

| Metric | Semantic | Hybrid |
|---|---:|---:|
| Successful measured requests | 96 | 96 |
| Supported document recall@5 | 91.67% | 97.92% |
| Unsupported questions with results | 35/48 | 35/48 |
| Irrelevant returned passages | 191 | 195 |
| Passage precision | 18.72% | 19.42% |
| Median request latency | 2.730 s | 3.503 s |
| p95 request latency | 3.653 s | 4.999 s |
| Semantic timeout requests | 0 | 0 |
| Keyword timeout requests | 0 | 0 |
| Keyword error requests (including timeouts) | 0 | 0 |
| Keyword fetch calls / requested records | 0 / 0 | 1152 / 1152 |
| Keyword fetch returned records | 0 | 1152 |
| Reported keyword fetch read units | 0 (no keyword calls) | 1152 |

Fetch usage was reported on 1152 of 1152 hybrid fetch calls. Missing usage remains unknown, not zero. These units cover keyword fetches, excluding semantic query usage, provisioning, warmup, and cleanup. They are not a currency estimate. The runner refreshed its synthetic session 1 time(s), outside measured search latency.

Both modes returned results for 35 of 48 questions whose requested facts were absent. The baseline also needs precision and answerability work; a relative improvement over it would not establish acceptable production precision.

| Category | Questions | Semantic recall@5 | Hybrid recall@5 | Unsupported hits: semantic / hybrid |
|---|---:|---:|---:|---:|
| identifier | 12 | 75.00% | 100.00% | 0 / 0 |
| paraphrase | 24 | 95.83% | 95.83% | 0 / 0 |
| phrase | 12 | 100.00% | 100.00% | 0 / 0 |
| unsupported_far | 12 | n/a | n/a | 1 / 1 |
| unsupported_identifier | 24 | n/a | n/a | 22 / 22 |
| unsupported_near | 12 | n/a | n/a | 12 / 12 |

## Regression disposition

Recovered supported questions: IT-105-id, FIN-28-id, OPS-64-id. Per-question recall losses: none. New unsupported questions with hits: none.

Questions with more irrelevant passages under hybrid: IT-105-id-missing, OPS-64-id-missing, OPS-640-id-missing, negative-22, negative-23. Shared policy wording or an exact identifier can retrieve a policy even when its text does not contain the requested rule. These are unresolved relevance blockers; the feature flag stays false. The benchmark does not tune the cutoff or relax the resource bounds to obtain a pass.

All keyword passes in the corrected full run completed without errors or timeouts. The earlier attempt recorded two keyword deadlines and one optional-leg error with semantic fallback, and is retained separately. Existing focused tests also verify timeout fallback, permissions, active versions, and provider-pool isolation. The extra request-time namespace fetches remain a latency and usage tradeoff below the existing 64-chunk cap. No new production retrieval correctness fix is claimed from this comparison.

Two benchmark defects were addressed: failed requests stay in the recall/error/latency denominators, and long runs renew their session through the normal CSRF-protected refresh flow before timing a search. Output safeguards prevent destroying labels or an unresolved cleanup journal. The first full attempt's token expired after 73 valid pairs, producing 23 pairs of authentication failures; it is preserved as retrieval-hybrid-expanded-v1-expired-session.json and is not used as the valid full-set quality comparison. The corrected run measured all 192 searches successfully.

## Verification and next action

All 26 focused evaluation and retrieval regression tests passed after the session fix. Backend Ruff and focused formatting passed. Both benchmark runs acknowledged cleanup of their exact synthetic namespaces, disposable databases, and rate-limit state. The canonical dataset is backend/tests/fixtures/retrieval-hybrid-expanded-v1.json; its SHA-256 is ed462bbaaf91d56e8e9f35ab671cab5dacde1e26750b19fcbf8f9a293dae7306. Full observations, category metrics, gates, configuration, and timestamps are in evaluation/retrieval-hybrid-expanded-v1-results.json. See docs/hybrid-evaluation.md for reproduction and limits.

Keep RETRIEVAL_HYBRID_ENABLED=false. Before another enablement decision, address keyword candidate precision and the cost of scanning namespace records during each request, then repeat this unchanged labeled set and a separately labeled representative company set. Repeated timing trials and concurrent-load acceptance are also needed. Do not infer organization-wide quality or production capacity from these 12 related synthetic policies.
