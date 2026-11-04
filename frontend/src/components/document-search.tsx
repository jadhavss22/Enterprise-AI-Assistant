"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth, type ApiError } from "../lib/auth-context";

type Passage = { document_id: string; filename: string; index_version: number; chunk_id: string; chunk_index: number; page: number | null; text: string; source_path: string };
type SearchResult = { status: "MATCHES" | "NO_ACCESSIBLE_DOCUMENTS" | "NO_RELEVANT_MATCHES"; passages: Passage[]; request_id: string };
type Availability = { status: "ready" | "warming" | "failed" | "unavailable"; model: string; vectors: string; code: string | null; retry_after: number; request_id?: string };
type Issue = { message: string; code: string; requestId?: string; retryUntil: number; observedAt?: number };

function issueFor(cause: unknown): Issue {
  const error = cause as ApiError;
  const observedAt = Date.now();
  const numeric = Number(error?.retryAfter);
  const retryUntil = error?.retryAfter ? (Number.isFinite(numeric) ? observedAt + Math.max(0, numeric) * 1000 : Date.parse(error.retryAfter)) : 0;
  return { observedAt, message: error?.message ?? "Search is temporarily unavailable.", code: error?.code ?? "SEARCH_UNAVAILABLE", requestId: error?.requestId, retryUntil: Number.isFinite(retryUntil) ? retryUntil : 0 };
}

function availabilityMessage(state: Availability) {
  if (state.status === "ready") return "Search services are ready.";
  if (state.status === "warming") return "The search model is loading. Availability will update automatically; your question has not been submitted.";
  if (state.code === "WORKER_UNAVAILABLE") return "The search worker is unavailable. Start it, then check availability.";
  if (state.code === "MODEL_CACHE_UNAVAILABLE") return "The local model cache is missing or invalid. An administrator must provision the model and restart the worker.";
  if (state.code === "VECTOR_UNAVAILABLE") return "The vector search service is unavailable. Check internet/provider connectivity, then check availability.";
  if (state.code === "PROVIDER_BUSY") return "Search provider capacity is busy. Availability will update shortly.";
  if (state.status === "failed") return "Search initialization or configuration failed. Contact your administrator.";
  return "Search readiness is unavailable. Check the local services and try again.";
}

export function DocumentSearch() {
  const { api, download, user, status } = useAuth();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [issue, setIssue] = useState<Issue | null>(null);
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [checking, setChecking] = useState(false);
  const [clock, setClock] = useState(0);
  const [result, setResult] = useState<{ owner: string; data: SearchResult } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const identity = `${user?.user_id}:${user?.department_id}:${user?.role}`;
  const visible = status === "authenticated" && result?.owner === identity ? result.data : null;
  const wait = Math.max(0, Math.ceil(((issue?.retryUntil ?? 0) - clock) / 1000));

  const checkAvailability = useCallback(async (signal?: AbortSignal) => {
    setChecking(true);
    try {
      const state = await api<Availability>("/retrieval/readiness", { signal });
      if (signal?.aborted) return;
      setAvailability(state);
      if (state.status === "ready") setIssue(current => current && ["MODEL_WARMING", "WORKER_UNAVAILABLE"].includes(current.code) ? null : current);
    } catch (cause) {
      if (!signal?.aborted) {
        setAvailability({ status: "unavailable", model: "unknown", vectors: "unknown", code: "READINESS_UNAVAILABLE", retry_after: 5 });
        setIssue(issueFor(cause)); setClock(Date.now());
      }
    } finally { if (!signal?.aborted) setChecking(false); }
  }, [api]);

  useEffect(() => {
    if (status !== "authenticated") return;
    const request = new AbortController();
    void Promise.resolve().then(() => checkAvailability(request.signal));
    return () => { request.abort(); controller.current?.abort("session"); };
  }, [checkAvailability, status, identity]);

  useEffect(() => {
    if (!availability || !["warming", "unavailable"].includes(availability.status) || status !== "authenticated") return;
    const request = new AbortController();
    const timer = window.setTimeout(() => void checkAvailability(request.signal), Math.max(3, availability.retry_after) * 1000);
    return () => { request.abort(); window.clearTimeout(timer); };
  }, [availability, checkAvailability, status]);

  useEffect(() => {
    if (!issue?.retryUntil) return;
    const timer = window.setInterval(() => { setClock(Date.now()); if (Date.now() >= issue.retryUntil) window.clearInterval(timer); }, 1000);
    return () => window.clearInterval(timer);
  }, [issue?.retryUntil]);

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || busy || wait > 0 || availability?.status !== "ready") return;
    controller.current?.abort("superseded");
    const request = new AbortController();
    controller.current = request;
    const timeout = window.setTimeout(() => request.abort("timeout"), 50000);
    setBusy(true); setIssue(null); setResult(null);
    try {
      const data = await api<SearchResult>("/retrieval/search", { method: "POST", body: JSON.stringify({ query: query.trim() }), signal: request.signal });
      if (!request.signal.aborted) setResult({ owner: identity, data });
    } catch (cause) {
      if (controller.current === request) {
        if (request.signal.aborted) setIssue({ message: request.signal.reason === "user" ? "Search cancelled." : "Search timed out. Check availability before retrying.", code: request.signal.reason === "user" ? "SEARCH_CANCELLED" : "SEARCH_TIMEOUT", retryUntil: 0 });
        else {
          const next = issueFor(cause); setIssue(next); setClock(Date.now());
          if (["MODEL_WARMING", "WORKER_UNAVAILABLE", "MODEL_INIT_FAILED", "MODEL_CACHE_UNAVAILABLE", "SEARCH_UNAVAILABLE", "VECTOR_UNAVAILABLE"].includes(next.code)) void checkAvailability();
        }
      }
    } finally { window.clearTimeout(timeout); if (controller.current === request) setBusy(false); }
  }

  async function openSource(passage: Passage) {
    setIssue(null);
    try { await download(passage.source_path, passage.filename); }
    catch (cause) { const next = issueFor(cause); setResult(null); setIssue(next); setClock(next.observedAt ?? 0); }
  }

  return <section className="panel document-search" aria-labelledby="document-search-heading">
    <h2 id="document-search-heading">Search documents</h2>
    <p className="muted-note">Find matching passages in documents you can access. These are source passages, not an AI-generated answer.</p>
    <div role="status"><p>{availability ? availabilityMessage(availability) : "Checking search availability…"}</p>{availability?.status !== "ready" && availability?.request_id && <p className="muted-note">Availability request ID: {availability.request_id}</p>}<button type="button" className="button subtle small-button" disabled={checking} onClick={() => void checkAvailability()}>{checking ? "Checking…" : "Check search availability"}</button></div>
    <form className="document-upload-form" onSubmit={(event) => void search(event)}>
      <label htmlFor="document-question">Your question</label>
      <textarea id="document-question" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={2000} rows={3} required placeholder="For example: What are the support hours?" />
      <p className="muted-note">Keep questions short. Long questions that exceed the embedding model’s token limit are rejected.</p>
      <div><button className="button primary" disabled={busy || !query.trim() || wait > 0 || availability?.status !== "ready"} type="submit">{busy ? "Searching…" : "Search passages"}</button>{busy && <button className="button subtle" type="button" onClick={() => controller.current?.abort("user")}>Cancel</button>}</div>
    </form>
    {issue && <div className="inline-error" role="alert"><p>{issue.message}</p>{wait > 0 && <p>Retry in {wait} seconds.</p>}<p className="muted-note">Code: {issue.code}{issue.requestId ? ` · Request ID: ${issue.requestId}` : ""}</p></div>}
    <div aria-live="polite" aria-busy={busy}>
      {visible?.status === "NO_ACCESSIBLE_DOCUMENTS" && <p>No searchable documents are available to your account yet.</p>}
      {visible?.status === "NO_RELEVANT_MATCHES" && <p>No relevant passages were found. Try a more specific question.</p>}
      {visible?.passages.map((passage) => <article className="search-passage" key={passage.chunk_id}>
        <h3>{passage.filename}</h3><p className="muted-note">{passage.page ? `Page ${passage.page} · ` : ""}Passage {passage.chunk_index + 1}</p>
        <p className="passage-text">{passage.text}</p>
        <button className="button subtle small-button" type="button" onClick={() => void openSource(passage)}>Download source</button>
      </article>)}
    </div>
  </section>;
}
