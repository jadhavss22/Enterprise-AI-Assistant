# System Design Document

Source: [System_Design.docx](./System_Design.docx), version 1.0, 14 October 2026.

This Markdown companion records the approved architecture baseline. The Word original remains preserved and authoritative for the complete schema and API tables.

## Approved architecture

- Next.js with TypeScript and App Router frontend in `frontend/`.
- FastAPI + Pydantic modular monolith backend.
- Separate Celery worker process using Redis.
- MongoDB as authority for users, ACLs, conversations, jobs, audits, and later approvals.
- Azure Blob Storage for originals; Azurite locally.
- Pinecone for versioned vectors.
- Azure OpenAI/OpenAI-compatible model adapters for generation and embeddings.
- Versioned REST endpoints under `/api/v1`.
- Authenticated POST `fetch` streaming with SSE frames; native `EventSource` is not used.

## Security invariants

- Every protected request validates JWT, active session, account status, current role, and applicable document authorization.
- Deleted or unauthorized documents never contribute to new answers or source downloads.
- Retryable work uses stable idempotency keys, atomic transitions, leases, and fencing where required.
- Security logs contain metadata only and never raw prompts, answers, or document text.
- Admin access to private documents is audited.
- Saved-answer and citation access is rechecked against current source permissions.

## Phase 1 processing baseline

Upload is authenticated, validated, hashed, and recorded with a durable ingestion job. A worker extracts text, chunks it, creates embeddings, stages a Pinecone index version, atomically marks the active version, and cleans old vectors asynchronously. Failed jobs expose safe summaries and support controlled retry.

Starting values such as approximately 600-token chunks, 100-token overlap, Top 20 candidates, Top 5 final chunks, six-message memory, and a 2,000-token budget are configurable and must be benchmarked.

## Local and cloud deployment

Local development uses Docker Compose with frontend, API, Celery, MongoDB, Redis, and Azurite. The provisional Next.js cloud target is Azure Container Apps. Final hosting validation and paid resource provisioning are deferred. Azure Static Web Apps remains a deployment option to validate separately against the selected App Router rendering and streaming behavior.

## Phase 4 boundary

LangGraph and simulated MongoDB IT tickets are deferred until RAG and production hardening are stable. Every action requires explicit user approval, immutable draft verification, authorization recheck, idempotency, and audit logging.

