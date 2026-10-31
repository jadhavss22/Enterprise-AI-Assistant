"use client";

import { useCallback, useEffect, useState } from "react";
import { PlaceholderShell } from "./placeholder-shell";
import { useAuth, type ApiError } from "../lib/auth-context";

type Job = { job_id: string; document_id: string; status: "QUEUED" | "PROCESSING" | "SUCCEEDED" | "FAILED"; stage: string | null; attempt_count: number; max_attempts: number; error_code: string | null; error_message: string | null; index_cleanup_status: string | null; index_cleanup_error_code: string | null; created_at: string; updated_at: string };
type Page = { jobs: Job[]; next_cursor: string | null };

export function AdminIngestionJobs() {
  const { api } = useAuth();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const currentCursor = cursorStack[cursorStack.length - 1];
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    const query = new URLSearchParams({ limit: "50" });
    if (currentCursor) query.set("cursor", currentCursor);
    if (statusFilter) query.set("status", statusFilter);
    try {
      const page = await api<Page>(`/admin/ingestion-jobs?${query.toString()}`, { signal });
      if (signal?.aborted) return;
      setNextCursor(page.next_cursor);
      setJobs(page.jobs); setError(null);
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === "AbortError")) {
        setError((cause as ApiError)?.message ?? "Could not load ingestion jobs.");
      }
    } finally { setLoading(false); }
  }, [api, currentCursor, statusFilter]);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.resolve().then(() => load(controller.signal));
    return () => controller.abort();
  }, [load]);

  async function retry(jobId: string) {
    setBusyJob(jobId); setError(null); setMessage(null);
    try {
      const accepted = await api<{ job_id: string }>(`/admin/ingestion-jobs/${encodeURIComponent(jobId)}/retry`, { method: "POST" });
      setMessage(`A new processing attempt was queued (${accepted.job_id}).`);
      if (currentCursor) setCursorStack([null]);
      else await load();
    } catch (cause) { setError((cause as ApiError)?.message ?? "Retry could not be queued."); }
    finally { setBusyJob(null); }
  }

  return <PlaceholderShell title="Ingestion jobs" roles={["admin"]}>
    <div className="page-wrap documents-page">
      <div className="page-heading"><div><p className="eyebrow">ADMINISTRATION</p><h1>Ingestion jobs</h1><p>Review processing stages and safely retry failed jobs.</p></div></div>
      {message && <p className="inline-success" role="status">{message}</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      <section className="panel document-list">
        <div className="section-heading"><div><h2>Processing queue</h2><p className="muted-note">Only job metadata and safe error summaries are shown.</p></div><div className="job-controls"><label htmlFor="job-status">Status</label><select id="job-status" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setCursorStack([null]); }}><option value="">All</option><option value="QUEUED">Queued</option><option value="PROCESSING">Processing</option><option value="SUCCEEDED">Succeeded</option><option value="FAILED">Failed</option></select><button className="button subtle" onClick={() => void load()} disabled={loading}>Refresh</button></div></div>
        {loading ? <p className="table-message">Loading jobs…</p> : jobs.length === 0 ? <div className="empty-state"><strong>No ingestion jobs found</strong><p>Jobs will appear when documents are uploaded.</p></div> : <div className="admin-job-list">{jobs.map((job) => <article className="admin-job-row" key={job.job_id}><div><strong>{job.status} · {job.stage ?? "Queued"}</strong><span>Job {job.job_id} · Document {job.document_id}</span></div><div className="admin-job-detail"><span>Attempt {job.attempt_count} of {job.max_attempts}</span>{job.error_message && <span>{job.error_message}</span>}{job.index_cleanup_status && job.index_cleanup_status !== "NOT_REQUIRED" && <span>Old search-index cleanup: {job.index_cleanup_status}{job.index_cleanup_error_code ? ` (${job.index_cleanup_error_code})` : ""}</span>}</div>{job.status === "FAILED" && <button className="button subtle small-button" disabled={busyJob === job.job_id} onClick={() => void retry(job.job_id)}>{busyJob === job.job_id ? "Queueing…" : "Retry"}</button>}</article>)}</div>}
        <nav className="table-footer" aria-label="Ingestion job pages">
          <span aria-live="polite">Page {cursorStack.length}</span>
          <div className="button-row">
            <button className="button subtle" aria-label="Previous jobs" disabled={loading || cursorStack.length === 1} onClick={() => setCursorStack((stack) => stack.slice(0, -1))}>Previous</button>
            <button className="button subtle" aria-label="Next jobs" disabled={loading || !nextCursor} onClick={() => { if (nextCursor) setCursorStack((stack) => [...stack, nextCursor]); }}>Next</button>
          </div>
        </nav>
      </section>
    </div>
  </PlaceholderShell>;
}
