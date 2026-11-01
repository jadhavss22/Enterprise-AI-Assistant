"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { DocumentSearch } from "./document-search";
import { PlaceholderShell } from "./placeholder-shell";
import { useAuth, type ApiError } from "../lib/auth-context";

type DocumentRow = {
  document_id: string; filename: string; mime_type: string; size_bytes: number; uploaded_by: string;
  visibility: "PRIVATE" | "DEPARTMENT" | "ORGANIZATION";
  status: "QUEUED" | "PROCESSING" | "READY" | "FAILED" | "OCR_REQUIRED" | "DELETED";
  active_index_version: number | null; chunk_count: number; ingestion_status: string | null; ingestion_stage: string | null; created_at: string;
};
type Job = {
  job_id: string; document_id: string; status: "QUEUED" | "PROCESSING" | "SUCCEEDED" | "FAILED";
  stage: string | null; attempt_count: number; max_attempts: number;
  error_code: string | null; error_message: string | null;
};
type UploadAccepted = { document_id: string; job_id: string; status: string };
type Page = { documents: DocumentRow[]; next_cursor: string | null };
type ShareTarget = { user_id: string; display_name: string; role: string };
type ShareTargets = { users: ShareTarget[] };
type Permissions = { visibility: "PRIVATE" | "DEPARTMENT" | "ORGANIZATION"; allowed_user_ids: string[] };

const stageLabels: Record<string, string> = {
  VALIDATING: "Validating file", EXTRACTING: "Extracting text", CHUNKING: "Preparing text chunks",
  EMBEDDING: "Creating embeddings", INDEXING: "Adding to search index", VERIFYING: "Verifying index",
  COMPLETE: "Ready",
};
function messageFor(error: unknown): string {
  const value = error as ApiError;
  if (value?.code === "DUPLICATE_DOCUMENT") return "This file already exists in this visibility scope.";
  if (value?.code === "FILE_TOO_LARGE") return "Files must be 20 MB or smaller.";
  if (value?.status === 403) return "Your current role cannot upload documents with this visibility.";
  return value?.message ?? "The request failed. Check the local services and try again.";
}
function sizeLabel(bytes: number) {
  return (bytes / (1024 * 1024)).toFixed(bytes < 1024 * 1024 ? 2 : 1) + " MB";
}

export function DocumentWorkspace({ uploadOnly = false }: { uploadOnly?: boolean }) {
  const { api, download, user } = useAuth();
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const currentCursor = cursorStack[cursorStack.length - 1];
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [visibility, setVisibility] = useState<"PRIVATE" | "DEPARTMENT" | "ORGANIZATION">("DEPARTMENT");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sharingDocument, setSharingDocument] = useState<string | null>(null);
  const [shareTargets, setShareTargets] = useState<ShareTarget[]>([]);
  const [allowedUserIds, setAllowedUserIds] = useState<string[]>([]);
  const [shareVisibility, setShareVisibility] = useState<Permissions["visibility"]>("PRIVATE");
  const [savingPermissions, setSavingPermissions] = useState(false);

  const load = useCallback(async (signal?: AbortSignal, background = false) => {
    if (!background) setLoading(true);
    try {
      const query = new URLSearchParams({ limit: "50" });
      if (currentCursor) query.set("cursor", currentCursor);
      const result = await api<Page>(`/documents?${query}`, { signal });
      if (signal?.aborted) return;
      setDocuments(result.documents);
      setNextCursor(result.next_cursor);
      setError(null);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(messageFor(cause));
    } finally { if (!background) setLoading(false); }
  }, [api, currentCursor]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    if (!jobId) return;
    const currentJobId = jobId;
    const controller = new AbortController();
    let cancelled = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const latest = await api<Job>("/ingestion-jobs/" + encodeURIComponent(currentJobId), { signal: controller.signal });
        if (cancelled) return;
        setJob(latest);
        await load(controller.signal, true);
        if (cancelled) return;
        if (latest.status === "SUCCEEDED" || latest.status === "FAILED") {
          setJobId(null);
          return;
        }
      } catch (cause) {
        if (!cancelled) setError(messageFor(cause));
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 3000);
    }
    void poll();
    return () => { cancelled = true; controller.abort(); if (timer) window.clearTimeout(timer); };
  }, [api, jobId, load]);

  const hasPendingDocuments = documents.some((item) => item.status === "QUEUED" || item.status === "PROCESSING" || item.ingestion_status === "QUEUED" || item.ingestion_status === "PROCESSING");
  useEffect(() => {
    if (jobId || !hasPendingDocuments) return;
    const controller = new AbortController();
    let timer: number | undefined;
    async function refreshPending() {
      await load(controller.signal, true);
      if (!controller.signal.aborted) timer = window.setTimeout(() => void refreshPending(), 3000);
    }
    timer = window.setTimeout(() => void refreshPending(), 3000);
    return () => { controller.abort(); if (timer) window.clearTimeout(timer); };
  }, [hasPendingDocuments, jobId, load]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) { setError("Choose a PDF, DOCX, or TXT file first."); return; }
    setSaving(true); setError(null); setJob(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("visibility", visibility);
      const accepted = await api<UploadAccepted>("/documents", {
        method: "POST", body: form, headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      setFile(null);
      const input = document.getElementById("document-file") as HTMLInputElement | null;
      if (input) input.value = "";
      setJobId(accepted.job_id);
      if (currentCursor) setCursorStack([null]);
      else await load();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setSaving(false); }
  }

  async function openAccess(documentId: string) {
    setError(null);
    try {
      const [permissions, targets] = await Promise.all([
        api<Permissions>(`/documents/${encodeURIComponent(documentId)}/permissions`),
        api<ShareTargets>(`/documents/${encodeURIComponent(documentId)}/share-targets`),
      ]);
      setSharingDocument(documentId);
      setShareVisibility(permissions.visibility);
      setAllowedUserIds(permissions.allowed_user_ids);
      setShareTargets(targets.users);
    } catch (cause) { setError(messageFor(cause)); }
  }

  async function saveAccess(documentId: string) {
    setSavingPermissions(true); setError(null);
    try {
      await api(`/documents/${encodeURIComponent(documentId)}/permissions`, {
        method: "PATCH",
        body: JSON.stringify({
          visibility: shareVisibility,
          allowed_user_ids: shareVisibility === "PRIVATE" ? allowedUserIds : [],
        }),
      });
      setSharingDocument(null);
      setAllowedUserIds([]);
      await load();
    } catch (cause) { setError(messageFor(cause)); }
    finally { setSavingPermissions(false); }
  }

  const canUpload = user?.role === "manager" || user?.role === "admin";
  return <PlaceholderShell title={uploadOnly ? "Manager Uploads" : "Documents"} roles={uploadOnly ? ["manager", "admin"] : undefined}>
    <div className="page-wrap documents-page">
      <div className="page-heading"><div><p className="eyebrow">KNOWLEDGE LIBRARY</p><h1>{uploadOnly ? "Upload documents" : "Documents"}</h1><p>{uploadOnly ? "Upload a source file and follow its processing stages." : "Browse files your account is allowed to access."}</p></div>
        {!uploadOnly && canUpload && <Link className="button primary" href="/manager/upload">Upload document</Link>}
      </div>

      {!uploadOnly && <DocumentSearch />}

      {canUpload && <section className="panel document-upload">
        <div><h2>{uploadOnly ? "New upload" : "Add a document"}</h2><p className="muted-note">PDF, DOCX, or TXT · Maximum 20 MB</p></div>
        <form className="document-upload-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="document-file">Choose file</label>
          <input id="document-file" type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
          <label htmlFor="document-visibility">Visibility</label>
          <select id="document-visibility" value={visibility} onChange={(event) => setVisibility(event.target.value as typeof visibility)}>
            <option value="PRIVATE">Private · you (add colleagues after upload)</option>
            <option value="DEPARTMENT">Department · your department</option>
            {user?.role === "admin" && <option value="ORGANIZATION">Organization · everyone</option>}
          </select>
          <p className="muted-note">Only Admins can publish organization-wide documents. Files are checked before processing.</p>
          <button className="button primary" type="submit" disabled={saving || !file}>{saving ? "Uploading…" : "Upload and process"}</button>
        </form>
      </section>}

      {error && <p className="inline-error" role="alert">{error}</p>}
      {job && <section className={"panel ingestion-status " + job.status.toLowerCase()} role="status">
        <strong>{job.status === "SUCCEEDED" ? "Processing complete" : job.status === "FAILED" ? "Processing needs attention" : stageLabels[job.stage ?? ""] ?? "Processing document"}</strong>
        <p>{job.status === "FAILED" ? job.error_message ?? "The document could not be processed." : job.status === "SUCCEEDED" ? "The document is ready for the next project phase." : "Stage: " + (stageLabels[job.stage ?? ""] ?? "Queued") + ". Attempt " + job.attempt_count + " of " + job.max_attempts + "."}</p>
      </section>}

      <section className="panel document-list">
        <div className="section-heading"><div><h2>Accessible documents</h2><p className="muted-note">Access is checked by the API for each request.</p></div><button className="button subtle" onClick={() => void load()} disabled={loading}>Refresh</button></div>
        {loading ? <p className="table-message">Loading documents…</p> : documents.length === 0 ? <div className="empty-state"><strong>No accessible documents yet</strong><p>When documents are uploaded and authorized for your account, they appear here.</p></div> :
          <div className="document-rows">{documents.map((item) => <article className="document-row" key={item.document_id}>
            <div className="document-icon">▤</div><div className="document-info"><strong>{item.filename}</strong><span>{sizeLabel(item.size_bytes)} · {item.visibility.toLowerCase()}</span></div>
            <div className="document-state"><strong>{item.status === "PROCESSING" ? "PROCESSING" : item.status}</strong><span>{item.status === "PROCESSING" ? stageLabels[item.ingestion_stage ?? ""] ?? "Working" : item.status === "READY" ? item.chunk_count + " text chunks" : item.status === "OCR_REQUIRED" ? "Scanned PDF; OCR is not enabled" : item.status === "FAILED" ? "See ingestion job details" : ""}</span></div>
            {item.status !== "DELETED" && <div className="document-actions"><button className="button subtle small-button" onClick={() => void download("/documents/" + encodeURIComponent(item.document_id) + "/source", item.filename)}>Download source</button>{(user?.role === "admin" || (user?.role === "manager" && item.uploaded_by === user.user_id)) && <button className="button subtle small-button" onClick={() => void openAccess(item.document_id)}>Manage access</button>}</div>}
            {sharingDocument === item.document_id && <div className="document-share-panel"><div className="section-heading"><div><h3>Manage document access</h3><p className="muted-note">Private sharing is limited to active accounts in this document’s department.</p></div><button className="button subtle small-button" type="button" onClick={() => setSharingDocument(null)}>Close</button></div><label htmlFor={`visibility-${item.document_id}`}>Visibility</label><select id={`visibility-${item.document_id}`} value={shareVisibility} onChange={(event) => setShareVisibility(event.target.value as Permissions["visibility"])}><option value="PRIVATE">Private</option><option value="DEPARTMENT">Department</option>{user?.role === "admin" && <option value="ORGANIZATION">Organization</option>}</select>{shareVisibility === "PRIVATE" && <fieldset><legend>Authorize department colleagues</legend>{shareTargets.length === 0 ? <p className="muted-note">No other active accounts are in this department.</p> : shareTargets.map((target) => <label className="share-target" key={target.user_id}><input type="checkbox" checked={allowedUserIds.includes(target.user_id)} onChange={(event) => setAllowedUserIds((current) => event.target.checked ? [...current, target.user_id] : current.filter((id) => id !== target.user_id))} /><span>{target.display_name} · {target.role.toLowerCase()}</span></label>)}</fieldset>}<button className="button primary" type="button" disabled={savingPermissions} onClick={() => void saveAccess(item.document_id)}>{savingPermissions ? "Saving…" : "Save access"}</button></div>}
          </article>)}</div>}
        <nav className="table-footer" aria-label="Document pages">
          <span aria-live="polite">Page {cursorStack.length}</span>
          <div className="button-row">
            <button className="button subtle" aria-label="Previous documents" disabled={loading || saving || cursorStack.length === 1} onClick={() => { setSharingDocument(null); setCursorStack((stack) => stack.slice(0, -1)); }}>Previous</button>
            <button className="button subtle" aria-label="Next documents" disabled={loading || saving || !nextCursor} onClick={() => { if (nextCursor) { setSharingDocument(null); setCursorStack((stack) => [...stack, nextCursor]); } }}>Next</button>
          </div>
        </nav>
      </section>
    </div>
  </PlaceholderShell>;
}
