"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth, type ApiError } from "../lib/auth-context";

type Passage = { document_id: string; filename: string; index_version: number; chunk_id: string; chunk_index: number; page: number | null; text: string; source_path: string };
type SearchResult = { status: "MATCHES" | "NO_ACCESSIBLE_DOCUMENTS" | "NO_RELEVANT_MATCHES"; passages: Passage[]; request_id: string };

export function DocumentSearch() {
  const { api, download, user, status } = useAuth();
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ owner: string; data: SearchResult } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const identity = `${user?.user_id}:${user?.department_id}:${user?.role}`;
  useEffect(() => () => { controller.current?.abort(); }, [identity]);
  const visible = status === "authenticated" && result?.owner === identity ? result.data : null;

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || busy) return;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const timeout = window.setTimeout(() => request.abort(), 50000);
    setBusy(true); setError(null); setResult(null);
    try {
      const data = await api<SearchResult>("/retrieval/search", {
        method: "POST", body: JSON.stringify({ query: query.trim() }), signal: request.signal,
      });
      if (!request.signal.aborted) setResult({ owner: identity, data });
    } catch (cause) {
      if (controller.current === request) setError(request.signal.aborted ? "Search was cancelled or timed out. You can retry." : (cause as ApiError)?.message ?? "Search is temporarily unavailable.");
    } finally {
      window.clearTimeout(timeout);
      if (controller.current === request) setBusy(false);
    }
  }

  async function openSource(passage: Passage) {
    setError(null);
    try { await download(passage.source_path, passage.filename); }
    catch (cause) { setResult(null); setError((cause as ApiError)?.message ?? "Source access has changed. Search again."); }
  }

  return <section className="panel document-search" aria-labelledby="document-search-heading">
    <h2 id="document-search-heading">Search documents</h2>
    <p className="muted-note">Find matching passages in documents you can access. These are source passages, not an AI-generated answer.</p>
    <form className="document-upload-form" onSubmit={(event) => void search(event)}>
      <label htmlFor="document-question">Your question</label>
      <textarea id="document-question" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={2000} rows={3} required placeholder="For example: What are the support hours?" />
      <p className="muted-note">Keep questions short. Long questions that exceed the embedding model’s token limit are rejected.</p>
      <div><button className="button primary" disabled={busy || !query.trim()} type="submit">{busy ? "Searching…" : "Search passages"}</button>{busy && <button className="button subtle" type="button" onClick={() => controller.current?.abort()}>Cancel</button>}</div>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}
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
