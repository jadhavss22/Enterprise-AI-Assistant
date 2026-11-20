# Reranking and query rewriting

Both stages are opt-in backend settings and are shared by Document Search and Chat. They default to disabled. Normal retrieval and response schemas continue to use the existing semantic cutoff, candidate/result limits, and final account, document access, deletion, and active-version checks.

## Reranking

Set `RETRIEVAL_RERANK_ENABLED=true` on the API and embedding worker. Provision the pinned model cache explicitly before restarting the worker.

```sh
HF_HUB_OFFLINE=0 python -m scripts.provision_reranker
```

Run provisioning in the worker environment with the persistent Hugging Face cache mounted. The model is `cross-encoder/ms-marco-MiniLM-L6-v2` at revision `ce0834f22110de6d9222af7a7a03628121708969`, loaded on CPU with safetensors and remote code disabled. Worker startup warms the cached model. User queries cannot trigger a download or model initialization.

The worker scores the validated candidate list, and the API uses those scores to reorder it before selecting final passages. This stage preserves the semantic cutoff and performs no score-based rejection. It can also reorder the optional hybrid candidate list. The pre-existing callback-based reranking experiment remains separate.

`RETRIEVAL_RERANK_TIMEOUT_SECONDS` defaults to eight seconds. The optional provider pool is separate from required embedding/vector API work. The Celery inference task shares the existing single-concurrency worker, so benchmark worker contention before enabling it broadly. Input is limited to the configured candidate count and 100,000 combined characters. Task arguments carry authorized passage text transiently through trusted Redis infrastructure. Log representations redact the arguments, task results expire after 60 seconds, and completed results are forgotten. Missing cache, warming, timeout, invalid scores, or provider failures preserve the original candidate order.

## Query rewriting

Set `RETRIEVAL_QUERY_REWRITE_ENABLED=true` on the API to enable one alternative search after a successful retrieval returns `NO_RELEVANT_MATCHES`. Existing matches and `NO_ACCESSIBLE_DOCUMENTS` skip this stage. The existing Gemini configuration receives the original search question and is asked to preserve intent, qualifiers, negation, literal identifiers, numbers, and quoted terms. Invalid, unchanged, multiline, or overlong output is rejected. Numeric and identifier tokens must match, and common explicit negations, including contractions, are checked in both directions. These guards cover literal constraints; prompt intent still requires evaluation on labeled questions.

The rewrite has a five-second deadline configured with `RETRIEVAL_REWRITE_TIMEOUT_SECONDS`. Its single retrieval retry has a separate ten-second deadline configured with `RETRIEVAL_QUERY_RETRY_TIMEOUT_SECONDS`. Optional failures preserve the original miss. Authentication failures still propagate, and actor authorization is checked before retry and before returning a fallback.

Chat retains its existing history-based follow-up resolution. The new search rewrite runs inside shared retrieval, so the alternative phrasing does not replace the question used for grounded answer generation or the user's saved question.

This shared implementation performs at most one alternative search. The earlier chat-only prototype is absent from the current dev implementation. Chat retains one shared retrieval call, so the two retry paths cannot stack.

## Verification and evaluation

`tests/test_retrieval_quality.py` covers literal constraint validation, bounded failures, task redaction and score validation, cache-only loading, opt-in behavior, shared Search/Chat integration, original answer intent, and permission/version changes during reranking.

After provisioning and starting a worker with reranking enabled, compare baseline, reranking, rewriting, and combined behavior on the existing synthetic fixtures.

```sh
python -m scripts.retrieval_quality_experiment \
  --dataset ../evaluation/retrieval-fallback-v3.json \
  --output /tmp/retrieval-quality-results.json
```

Use the existing disposable evaluation setup and an isolated worker/Redis database as in the earlier retrieval benchmarks. The runner records per-question HTTP status, document recall, precision, unrelated-query hits, latency, and optional-stage execution counts. It fails if an enabled stage was never exercised, so a silent fallback cannot be reported as a successful model evaluation. Its four profiles run sequentially and provide an initial comparison. Use repeated paired runs and a larger labeled corpus before changing defaults. The earlier reranking and rewrite results remain historical calibration evidence.
