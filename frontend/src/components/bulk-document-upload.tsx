"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth, type ApiError } from "../lib/auth-context";

type Visibility = "PRIVATE" | "DEPARTMENT" | "ORGANIZATION";
type Phase = "waiting" | "uploading" | "upload_failed" | "processing" |
  "processing_failed" | "status_failed" | "ready" | "rejected";
type Accepted = { document_id: string; job_id: string };
type Job = {
  job_id: string; document_id: string; status: "QUEUED" | "PROCESSING" | "SUCCEEDED" | "FAILED";
  stage: string | null; attempt_count: number; max_attempts: number;
  ocr_current_page?: number | null; ocr_pages_completed?: number | null; ocr_pages_total?: number | null;
  error_code: string | null; error_message: string | null;
};
type UploadRow = {
  id: string; filename: string; size: number; file: File | null;
  visibility: Visibility; phase: Phase; percent: number;
  documentId?: string; jobId?: string; job?: Job; error?: string; busy?: boolean;
};

export const stageLabels: Record<string, string> = {
  VALIDATING: "Validating file", EXTRACTING: "Extracting text", CHUNKING: "Preparing text chunks",
  EMBEDDING: "Creating embeddings", INDEXING: "Adding to search index", VERIFYING: "Verifying index",
  COMPLETE: "Ready",
  OCR: "Reading scanned pages",
};

function errorMessage(cause: unknown): string {
  const error = cause as ApiError;
  let message = error.message || "The request failed. Try again.";
  if (error.code === "DUPLICATE_DOCUMENT") message = "This file already exists in this visibility scope.";
  if (error.status === 403) message = "Your current role cannot perform this action.";
  if (error.retryAfter) message += ` Retry after ${error.retryAfter} seconds.`;
  if (error.requestId) message += ` Request ID ${error.requestId}.`;
  return message;
}

function fileError(file: File): string | undefined {
  if (!/\.(pdf|docx|txt)$/i.test(file.name)) return "Choose a PDF, DOCX, or TXT file.";
  if (file.size === 0) return "This file is empty.";
  if (file.size > 20 * 1024 * 1024) return "Files must be 20 MB or smaller.";
}

function phaseLabel(row: UploadRow): string {
  switch (row.phase) {
    case "waiting": return "Waiting to upload";
    case "uploading": return row.percent === 100 ? "Upload sent · validating" : `Uploading · ${row.percent}%`;
    case "upload_failed": return "Upload needs attention";
    case "processing": return stageLabels[row.job?.stage ?? ""] ?? "Queued for processing";
    case "processing_failed": return row.job?.error_code === "OCR_REQUIRED" ? "OCR required" : "Processing failed";
    case "status_failed": return "Status unavailable";
    case "ready": return "Ready for search";
    case "rejected": return "File rejected";
  }
}

export function BulkDocumentUpload({ onActivity }: { onActivity: () => void }) {
  const { api, uploadDocument, user } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [visibility, setVisibility] = useState<Visibility>("DEPARTMENT");
  const [rows, setRows] = useState<UploadRow[]>([]);
  const rowsRef = useRef<UploadRow[]>([]);
  const activity = useRef(onActivity);
  const lifecycle = useRef<AbortController | null>(null);
  const uploadLock = useRef(false);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  function publish(next: UploadRow[]) {
    rowsRef.current = next;
    setRows(next);
  }

  function update(id: string, changes: Partial<UploadRow>) {
    publish(rowsRef.current.map(row => row.id === id ? { ...row, ...changes } : row));
  }

  useEffect(() => { activity.current = onActivity; }, [onActivity]);

  useEffect(() => {
    const controller = new AbortController();
    lifecycle.current = controller;
    return () => {
      controller.abort();
      lifecycle.current = null;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number;
    async function poll() {
      const pending = rowsRef.current.filter(row => row.phase === "processing" && row.jobId);
      let changed = false;
      for (const row of pending) {
        try {
          const job = await api<Job>(`/ingestion-jobs/${encodeURIComponent(row.jobId!)}`, {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]),
          });
          if (controller.signal.aborted) return;
          const current = rowsRef.current.find(item => item.id === row.id);
          if (current?.jobId !== job.job_id || current.phase !== "processing") continue;
          const phase = job.status === "SUCCEEDED" ? "ready" : job.status === "FAILED" ? "processing_failed" : "processing";
          const next: UploadRow[] = rowsRef.current.map(item => item.id === row.id ? {
            ...item, job, phase,
            error: job.status === "FAILED" ? job.error_message ?? "The document could not be processed." : undefined,
          } : item);
          rowsRef.current = next;
          setRows(next);
          changed = changed || phase !== "processing";
        } catch (cause) {
          if (controller.signal.aborted) return;
          const error = cause as ApiError;
          const next = rowsRef.current.map(item => item.id === row.id ? {
            ...item,
            phase: error.status === 403 || error.status === 404 ? "status_failed" as const : item.phase,
            error: errorMessage(cause),
          } : item);
          rowsRef.current = next;
          setRows(next);
        }
      }
      if (controller.signal.aborted) return;
      if (changed) activity.current();
      timer = window.setTimeout(() => void poll(), Math.max(3000, pending.length * 1000));
    }
    timer = window.setTimeout(() => void poll(), 1000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api]);

  async function transfer(row: UploadRow, signal: AbortSignal) {
    if (!row.file) return true;
    update(row.id, { phase: "uploading", percent: 0, error: undefined });
    const body = new FormData();
    body.append("file", row.file);
    body.append("visibility", row.visibility);
    try {
      const accepted = await uploadDocument<Accepted>({
        body, idempotencyKey: row.id, signal,
        onProgress: percent => {
          if (!signal.aborted) update(row.id, { percent });
        },
      });
      if (signal.aborted) return;
      if (!accepted || !/^[a-f0-9]{24}$/i.test(accepted.document_id) ||
          !/^[a-f0-9]{24}$/i.test(accepted.job_id)) {
        throw new Error("The server returned an incomplete upload response. Retry to check whether the file was accepted.");
      }
      update(row.id, {
        file: null, documentId: accepted.document_id, jobId: accepted.job_id,
        phase: "processing", percent: 100, job: undefined,
      });
      activity.current();
      return true;
    } catch (cause) {
      if (!signal.aborted) update(row.id, { phase: "upload_failed", error: errorMessage(cause) });
      return (cause as ApiError).status !== 429;
    }
  }

  async function startTransfers(batch: UploadRow[]) {
    const signal = lifecycle.current?.signal;
    if (!signal || signal.aborted || uploadLock.current) return;
    uploadLock.current = true;
    setUploading(true);
    try {
      for (const row of batch) {
        if (signal.aborted) break;
        const mayContinue = await transfer(row, signal);
        if (!mayContinue) {
          const message = rowsRef.current.find(item => item.id === row.id)?.error;
          for (const pending of batch) {
            if (rowsRef.current.find(item => item.id === pending.id)?.phase === "waiting") {
              update(pending.id, {
                phase: "upload_failed",
                error: `Batch paused after the upload limit was reached. ${message ?? "Retry later."}`,
              });
            }
          }
          break;
        }
      }
    } finally {
      uploadLock.current = false;
      if (!signal.aborted) setUploading(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!files.length || uploadLock.current) return;
    const batch: UploadRow[] = files.map(file => {
      const error = fileError(file);
      return {
        id: crypto.randomUUID(), filename: file.name, size: file.size,
        file: error ? null : file, visibility, percent: 0,
        phase: error ? "rejected" : "waiting", error,
      };
    });
    publish([...rowsRef.current, ...batch]);
    setFiles([]);
    if (fileInput.current) fileInput.current.value = "";
    void startTransfers(batch.filter(row => row.phase === "waiting"));
  }

  async function retryProcessing(row: UploadRow) {
    const signal = lifecycle.current?.signal;
    const current = rowsRef.current.find(item => item.id === row.id);
    if (!signal || signal.aborted || current?.busy || !row.documentId) return;
    update(row.id, { busy: true, error: undefined });
    try {
      // Recover an accepted retry whose response was lost before creating another job.
      const history = await api<{ jobs: Job[] }>(`/documents/${row.documentId}/jobs`, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
      });
      signal.throwIfAborted();
      const latest = history.jobs[0];
      if (latest && (latest.job_id !== row.jobId || latest.status !== "FAILED")) {
        update(row.id, {
          jobId: latest.job_id, job: latest,
          phase: latest.status === "SUCCEEDED" ? "ready" : latest.status === "FAILED" ? "processing_failed" : "processing",
          error: latest.status === "FAILED" ? latest.error_message ?? "Processing failed." : undefined,
        });
      } else {
        const accepted = await api<Accepted>(`/documents/${row.documentId}/reprocess`, {
          method: "POST", signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]),
        });
        signal.throwIfAborted();
        update(row.id, { jobId: accepted.job_id, job: undefined, phase: "processing" });
      }
      activity.current();
    } catch (cause) {
      if (!signal.aborted) update(row.id, { error: errorMessage(cause) });
    } finally {
      if (!signal.aborted) update(row.id, { busy: false });
    }
  }

  return <section className="panel document-upload">
    <h2>Add documents</h2>
    <form className="document-upload-form" onSubmit={submit}>
      <label htmlFor="document-file">Choose files</label>
      <input id="document-file" ref={fileInput} type="file" multiple
        accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
        disabled={uploading} onChange={event => setFiles(Array.from(event.target.files ?? []))} />
      <label htmlFor="document-visibility">Visibility for selected files</label>
      <select id="document-visibility" value={visibility} disabled={uploading}
        onChange={event => setVisibility(event.target.value as Visibility)}>
        <option value="PRIVATE">Private · you (add colleagues after upload)</option>
        <option value="DEPARTMENT">Department · your department</option>
        {user?.role === "admin" && <option value="ORGANIZATION">Organization · everyone</option>}
      </select>
      <p className="muted-note">PDF, DOCX, or TXT · Up to 20 MB each. Keep this page open to track this batch and retry files.</p>
      <button className="button primary" type="submit" disabled={uploading || !files.length}>
        {uploading ? "Uploading files…" : files.length ? `Upload ${files.length} ${files.length === 1 ? "file" : "files"}` : "Upload files"}
      </button>
    </form>
    {rows.length > 0 && <>
      <div className="section-heading">
        <h3>Upload progress</h3>
        <button className="button subtle small-button" type="button"
          disabled={!rows.some(row => row.phase === "ready" || row.phase === "rejected")}
          onClick={() => publish(rowsRef.current.filter(row => row.phase !== "ready" && row.phase !== "rejected"))}>
          Clear completed
        </button>
      </div>
      <ul className="bulk-upload-list" aria-label="File upload progress">
        {rows.map(row => <li className="bulk-upload-row" key={row.id}>
          <div className="bulk-upload-name"><strong>{row.filename}</strong>
            <span>{(row.size / (1024 * 1024)).toFixed(2)} MB · {row.visibility.toLowerCase()}</span>
          </div>
          <div className="bulk-upload-status">
            <span role="status">{phaseLabel(row)}</span>
            {row.phase === "uploading" && <progress max={100} value={row.percent} aria-label={`Uploading ${row.filename}`} />}
            {row.phase === "processing" && <progress aria-label={`Processing ${row.filename}`} />}
            {row.job && row.phase === "processing" && <span>Attempt {row.job.attempt_count} of {row.job.max_attempts}</span>}
            {row.job?.stage === "OCR" &&
              <span>PDF page {row.job.ocr_current_page} · {row.job.ocr_pages_completed ?? 0} of {row.job.ocr_pages_total} scanned pages read</span>}
            {row.error && <p className="inline-error" role="alert">{row.error}</p>}
          </div>
          {row.phase === "upload_failed" && <button className="button subtle small-button" type="button"
            disabled={uploading} onClick={() => void startTransfers([row])}>Retry upload</button>}
          {row.phase === "processing_failed" &&
            <button className="button subtle small-button" type="button"
              disabled={row.busy} onClick={() => void retryProcessing(row)}>
              {row.busy ? "Retrying…" : "Retry processing"}
            </button>}
          {row.phase === "status_failed" && <button className="button subtle small-button" type="button"
            onClick={() => update(row.id, { phase: "processing", error: undefined })}>Check status</button>}
        </li>)}
      </ul>
    </>}
  </section>;
}
