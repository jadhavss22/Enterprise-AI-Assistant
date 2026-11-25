# Reranking evaluation configuration

The four-profile comparison uses the unchanged 96-question, 12-policy synthetic corpus from the hybrid evaluation. Profiles are semantic, hybrid, semantic plus reranking, and hybrid plus reranking. Query rewriting remains disabled. Each question runs all four profiles with the first profile rotated across questions. The default is one warmed round; additional rounds rotate order again. Existing production defaults remain disabled.

## Provision and reproduce

Provision in the worker environment with the persistent Hugging Face cache mounted:

```sh
docker compose run --rm --no-deps -e HF_HUB_OFFLINE=0 worker \
  python -m scripts.provision_reranker
```

The pinned model is cross-encoder/ms-marco-MiniLM-L6-v2 at revision ce0834f22110de6d9222af7a7a03628121708969. Loading uses safetensors, remote code disabled, CPU execution, maximum pair length 384 tokens, and inference batch size 8. User queries cannot initialize or download the model. Startup warms its local cache; the benchmark requires a successful model-backed probe and reranked warmups before measurement.

The tested worker uses the normal prefork pool with concurrency one, OMP_NUM_THREADS=1 and MKL_NUM_THREADS=1. It serves only the isolated rerank-evaluation queue. Both query embeddings and reranking share that same execution process. Provision before starting this worker:

```sh
docker compose run -d --no-deps --name rerank-evaluation-worker \
  -e RETRIEVAL_RERANK_ENABLED=true \
  -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1 worker \
  celery -A app.workers.celery_app.celery_app worker \
  --loglevel=WARNING --queues=rerank-evaluation \
  --include=scripts.rerank_eval_tasks

docker compose run --rm --no-deps -v "$PWD/evaluation:/evaluation" api \
  python -m scripts.rerank_expanded_benchmark \
  --dataset tests/fixtures/retrieval-hybrid-expanded-v1.json \
  --output /evaluation/retrieval-rerank-expanded-v1-results.json \
  --rounds 1 --allow-synthetic-writes
```

Run from the repository root in a shell supporting the shown POSIX volume syntax; use the absolute evaluation directory on Windows. Start local MongoDB, its replica-set initialization, and Redis first. The runner routes only its own task publications to the isolated queue. It reuses the reviewed synthetic provisioning, unique temporary output files, exclusive journal ownership, session refresh, cancellation drain, and exact-namespace cleanup. Existing document vectors and users are not modified. The model cache intentionally persists for reuse. Stop and remove only the temporary evaluation containers when finished.

## Metrics and interpretation

Document recall@5, irrelevant passages, unsupported-question hit rate, latency, fetch usage, and provider errors use the same definitions as the hybrid comparison. Ranking adds mean reciprocal rank, binary document nDCG@5, and top-one relevance over supported questions. Duplicate source names cannot inflate nDCG. Failed supported requests score zero and remain in denominators. Unsupported questions are evaluated through their hit rate and irrelevant passages, not an invented ideal ranking.

The journal records candidate order before and after model ranking, stage completions, and fallbacks per request. It fails if either enabled reranking profile never exercises the real model. A profile with fallbacks fails its acceptance gate even if it returned HTTP 200. Reranking reorders existing approved candidates; it does not lower the semantic cutoff, recover candidates absent from the input pool, or reject low model logits. It therefore cannot independently provide unsupported-question abstention.

Timing covers the authenticated in-process API request, execution-worker round trips, Pinecone calls, and optional reranking. Model download, initialization, warmups, and proactive session refresh are excluded. All profiles use the same warmed worker with both models resident. This measures inference effects on that configuration, not the memory difference versus an embedding-only deployment. The corpus has one short chunk per policy and related synthetic question families, so larger documents and independent representative labels still require acceptance.

## Worker contention

After the quality run, an explicitly loaded diagnostic task invokes the production embedding or reranking function on synthetic inputs. It returns timings and validity only. It executes 12 isolated jobs of each kind, then six four-job waves. Each wave includes three maximum-candidate-count reranks and one embedding, alternating whether the embedding is submitted first or last. Rerank jobs contain 20 short synthetic query/passage pairs at the default candidate limit. This is a controlled burst-sensitivity probe, not a production arrival-rate or API-admission load test.

Worker timestamps measure publish-to-start delay, model execution, and publish-to-worker-completion time. Client and worker share the same Docker host clock. The latter excludes result-delivery polling; API latencies remain separately recorded above. Longer passages, ingestion work, multiple API processes, and sustained traffic are not modeled. Raw per-job observations, wave ordering, error counts, and median/p95 summaries remain in the journal.

## Declared acceptance gates

Each reranked profile is compared with its own semantic or hybrid baseline. Gates require supported ranking observations, all requests successful, no recorded keyword or semantic provider errors in either compared profile (including HTTP 200 fallbacks), real model execution, no rerank fallbacks, improved nDCG, nondecreasing MRR, no per-question recall losses, no increase in irrelevant passages or unsupported hits, and p95 API latency within baseline times 1.2 plus 250 ms. Mixed-wave embedding p95 completion time must also remain within isolated embedding p95 times 1.2 plus 250 ms, with no invalid diagnostic jobs.

Passing synthetic gates permits representative acceptance; it does not automatically enable a feature. A failed gate keeps reranking disabled. Hybrid's earlier independent enablement decision remains in force, and query rewriting requires its own evaluation. See the dated decision report and raw observations for the measured outcome.
