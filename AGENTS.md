# Repository instructions

## Resume and handoff

- Read `PROJECT_STATUS.md` before starting feature work. It is the durable current handoff; chat history is optional context.
- Inspect the actual branch, Git status and relevant specifications before editing. Reconcile newer code with the tracker rather than overwriting it.
- Update `PROJECT_STATUS.md` whenever a feature is completed and whenever work stops with meaningful unfinished changes. Record scope, decisions, verification results and limits, open issues, and concrete next steps.
- Include the tracker update with the feature checkpoint. Never claim tests, provider integration, commits or pushes succeeded unless verified. Distinguish earlier results from fresh checks.
- Never store credentials, tokens, passwords, secrets or sensitive document content in documentation.

## Git workflow

- The user requires onward development and commits on `dev`; keep `main` unchanged unless explicitly instructed otherwise.
- Preserve unrelated local changes. Do not auto-push or merge without user authorization.

## Project boundaries

- Follow the approved PRD/SDD and recorded decisions. FastAPI owns backend authentication, authorization, document processing and RAG; do not duplicate these in Next.js API routes.
- Read applicable nested instructions, including `frontend/AGENTS.md`, before working in those directories.
