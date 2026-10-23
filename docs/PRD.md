# Product Requirements Document

Source: [PRD.docx](./PRD.docx), version 1.1, 13 October 2026.

This Markdown companion preserves the approved product baseline while making proposals and unresolved decisions explicit. The Word original remains authoritative for wording and layout.

## Approved product scope

The product is an authenticated internal enterprise knowledge assistant. Employees ask questions over authorized enterprise documents and receive grounded, citation-backed answers. The initial deployment uses sample documents and test accounts. Operational agent actions are deferred until the RAG system is evaluated.

Approved goals include fast discovery, clickable source references, server-side authorization at every retrieval/display boundary, observable ingestion and failures, and a controlled foundation for later human-approved operations.

## Roles and document visibility

| Role | Approved behavior |
|---|---|
| Employee | Authenticate, view permitted documents, ask questions, manage own chats, provide feedback |
| Manager | Employee capabilities plus upload, delete, and reprocess own uploads |
| Admin | Manage users and roles, manage all documents, view dashboards/jobs, access all documents with audit logging |

Visibility is Private, Department, or Organization. Private documents are available to the uploader, explicitly authorized users in the uploader's department, and Admin. Department documents are available to authorized department members and Admin. Organization documents are available to authenticated users. Admin access is explicit and auditable.

## Approved MVP requirements

- Admin-created accounts with password authentication, securely hashed passwords, JWT expiry, and role checks.
- Server-side Employee/Manager/Admin RBAC and document-level visibility enforcement.
- PDF, DOCX, and TXT uploads up to 20 MB with actual-content validation and sanitized filenames.
- SHA-256 duplicate detection within the selected visibility scope.
- Originals in Azure Blob Storage, metadata in MongoDB, and vectors in Pinecone.
- Celery and Redis processing with staged statuses, retries up to three attempts, and idempotent reprocessing.
- Grounded answers only from authorized evidence, with verified clickable citations.
- Authenticated progressive streaming, multiple-document answers, and explicit insufficient-evidence responses.
- Personal conversations, feedback, audit logs, basic rate limits, and a lightweight Admin dashboard.
- Deleted or inaccessible source documents must be excluded from retrieval and source display.
- Saved answers that rely on inaccessible documents must be hidden and their source access rechecked.
- Agentic operations, external ITSM integrations, MCP-first implementation, multi-agent orchestration, and autonomous sensitive actions are deferred.

## Phases

| Phase | Outcome |
|---|---|
| 1 | Staged MVP with JWT/RBAC, uploads, ingestion, semantic RAG, citations, streaming, chats, feedback, dashboard, audit, and Docker local development |
| 2 | Hybrid retrieval, reranking, evaluation expansion, and ingestion improvements |
| 3 | Production hardening, Azure deployment, CI/CD, monitoring, security, performance, backup, and recovery |
| 4 | Controlled LangGraph simulated ticket workflow with mandatory approval |

## Open decisions

Duplicate scope, department membership, audit retention, purge timing, provider model IDs, quality thresholds, latency/concurrency targets, cloud SKUs, staging promotion rules, and sample datasets remain implementation-time decisions unless separately approved.

