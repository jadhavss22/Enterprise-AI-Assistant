# Permission-aware retrieval: approved slice, 31 October 2026



Source requirements: PRD FR-04/FR-06 and SDD section 6.1. The user approved standalone semantic retrieval, a Documents search panel, current per-document-version namespaces with explicit bounds, and existing CPU MiniLM embeddings. Query rewrite, generation, streaming, hybrid retrieval and reranking remain later slices, not cancelled requirements.



POST /api/v1/retrieval/search accepts {"query":"What are the support hours?"}. Server-controlled settings select up to 20 candidates and five final passages. Responses contain status MATCHES, NO_ACCESSIBLE_DOCUMENTS or NO_RELEVANT_MATCHES, passages and request_id. A passage includes authoritative filename, document ID, active index version, deterministic chunk ID/index, optional page, text and backend source_path. Similarity is a ranking diagnostic, never a confidence percentage; scores are not exposed in the UI.



MongoDB authorizes documents before namespace search and revalidates session/account/role/current ACL/deletion/active version before returning passages. No raw question or text is logged. Admin private-source reads are audited. Existing active versions remain searchable during reprocessing. Provider failures produce 503, not a misleading empty result or partial success. Scope above the configured bound produces 422, without silently omitting documents.



Starting limits: 2 admitted searches per API process; 25 documents; 4 concurrent namespace calls per search; 20 candidates per namespace merged to 20 globally; 5 final chunks; 10 searches/user/minute; 2000 characters plus model-specific token validation; 20-second worker wait and 20-second search deadline. The local model permits 254 content tokens. A 0.25 cosine cutoff is provisional and must be calibrated with a versioned dataset. These are development settings, not production performance commitments.



Query embeddings run on the existing single-concurrency Celery worker. Args are redacted in Celery events/log representations; the broker necessarily carries the query transiently. Task results expire after 60 seconds and are forgotten after use; Redis is trusted application infrastructure, not an audit store. Busy/cold workers may time out; queued queries expire and are revoked on timeout without terminating the shared worker. No new provider subscription or dependencies are required.



Validation: real-auth isolated Mongo/Redis route tests with provider doubles; worker query token-limit checks; namespace/identity/version/score validation; permission and version changes during search; disabled/revoked sessions; Admin private-read audit; rate/body bounds; synthetic live retrieval and labeled Recall@K baseline when local services/providers are available. Source fixtures contain invented information only.



## Initial synthetic calibration



Live ASGI route evaluation used disposable authentication/Mongo state and real Celery/MiniLM/Pinecone against the three already-uploaded invented fixtures. All 8 requests returned 200. At cutoff 0.3, mean source-document Recall@5 on six relevant questions was 0.8333 (leave approval missed); both unrelated questions returned no passages. At cutoff 0.25, Recall@5 was 1.0 on the same six questions and both negatives still returned no passages. Default 0.25 was selected from this tiny calibration set; it is not an independent holdout result or a production quality threshold. Some questions also return less relevant passages because the sample chunks mix topics; expanded datasets, precision/MRR measurements and later reranking remain necessary.




## Fresh verification — 31 October 2026

Full backend suite: 118 passed in 347.37 seconds with real isolated MongoDB/Redis and the Azurite synthetic transport test enabled. Ruff, full frontend ESLint and TypeScript checks, dependency-lock check (126 packages) and diff checks passed. An isolated webpack production build passed; the initial sandboxed build could not spawn a worker (EPERM), and the approved rerun passed. The main dev server/build output was not overwritten.

The headless production UI regression frontend/tests/retrieval-smoke.cjs passed with synthetic API responses: authenticated POST, passage/source rendering, empty and no-document states, provider and long-query errors, and revoked-source download handling. No page errors were observed; this test does not use real accounts or providers. Live worker/Pinecone route evaluation described above uses separate disposable authentication state and no vector upserts. User Chrome acceptance for this new panel is the remaining manual check.

The baseline labels identify relevant source documents, so the reported mean Recall@5 is source-document recall, not a chunk-level retrieval or answer-quality score. The JSON result summary preserves both cutoff experiments. Reindex before changing the embedding model; equal dimensions alone do not make different embedding spaces compatible. For more than 25 searchable documents, explicitly raise validated bounds or redesign index layout; no unsupported claim of large-scale performance is made.
