"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useAuth, type ApiError } from "../lib/auth-context";
import { AnswerFeedback, type Feedback } from "./answer-feedback";
import { PlaceholderShell } from "./placeholder-shell";

type Conversation = { conversation_id: string; title: string };
type Citation = { source_id: string; document_id: string; filename: string; page: number | null; source_path: string };
type Turn = { turn_id: string; client_request_id: string; question: string; answer: string; citations: Citation[]; status: string; is_complete: boolean; feedback?: Feedback | null };
type Page = { conversations: Conversation[]; next_cursor: string | null };
type MessagePage = { turns: Turn[]; next_cursor: string | null };
type Issue = { message: string; code?: string; requestId?: string; retryUntil?: number };

function issueFor(error: unknown): Issue {
  const value = error as ApiError;
  const delay = Number(value.retryAfter);
  return { message: value.message || "Chat is unavailable. Retry shortly.", code: value.code, requestId: value.requestId, retryUntil: Number.isFinite(delay) ? Date.now() + delay * 1000 : 0 };
}

export function ChatWorkspace({ historyOnly = false }: { historyOnly?: boolean }) {
  return <PlaceholderShell title={historyOnly ? "Chat History" : "AI Chat"}><ChatIdentity historyOnly={historyOnly} /></PlaceholderShell>;
}

function ChatIdentity({ historyOnly }: { historyOnly: boolean }) {
  const { user } = useAuth();
  return <ChatPanel key={`${user?.user_id}:${user?.department_id}:${user?.role}`} historyOnly={historyOnly} />;
}

function ChatPanel({ historyOnly }: { historyOnly: boolean }) {
  const { api, streamChat, download } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [listCursor, setListCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [partial, setPartial] = useState("");
  const [issue, setIssue] = useState<Issue | null>(null);
  const [wait, setWait] = useState(0);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<{ cid: string; question: string; client_request_id: string } | null>(null);
  const mounted = useRef(true);
  const selection = useRef<string | null>(null);
  const oldestLoaded = useRef<string | null>(null);
  const messageRequest = useRef(0);

  const loadList = useCallback(async (cursor?: string) => {
    const page = await api<Page>(`/conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`);
    if (!mounted.current) return;
    setConversations(old => cursor ? [...old, ...page.conversations] : page.conversations);
    setListCursor(page.next_cursor);
  }, [api]);

  const loadMessages = useCallback(async (cid: string, cursor?: string) => {
    const request = ++messageRequest.current;
    const boundary = oldestLoaded.current;
    let next = cursor;
    let refreshed: Turn[] = [];
    let nextCursor: string | null = null;
    do {
      const page = await api<MessagePage>(`/conversations/${cid}/messages${next ? `?cursor=${encodeURIComponent(next)}` : ""}`);
      if (!mounted.current || selection.current !== cid || request !== messageRequest.current) return;
      refreshed = [...page.turns, ...refreshed];
      nextCursor = page.next_cursor;
      if (cursor || !boundary || refreshed.some(turn => turn.turn_id === boundary)) break;
      next = nextCursor || undefined;
    } while (next);
    oldestLoaded.current = refreshed[0]?.turn_id || null;
    setTurns(old => cursor ? [...refreshed, ...old.filter(turn => !refreshed.some(fresh => fresh.turn_id === turn.turn_id))] : refreshed);
    setMessageCursor(nextCursor);
  }, [api]);

  useEffect(() => {
    mounted.current = true;
    void loadList().catch(error => mounted.current && setIssue(issueFor(error)));
    return () => { mounted.current = false; };
  }, [loadList]);
  useEffect(() => () => { controller.current?.abort("unmount"); }, []);
  useEffect(() => {
    const cid = new URLSearchParams(window.location.search).get("conversation");
    if (!historyOnly && cid && /^[a-f0-9]{24}$/.test(cid)) {
      const timer = window.setTimeout(() => { selection.current = cid; setSelected(cid); }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [historyOnly]);
  useEffect(() => {
    if (!selected) return;
    void loadMessages(selected).catch(error => mounted.current && setIssue(issueFor(error)));
  }, [selected, loadMessages]);
  useEffect(() => {
    if (!selected || busy) return;
    const refresh = () => { if (document.visibilityState === "visible") void loadMessages(selected).catch(error => setIssue(issueFor(error))); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [selected, busy, loadMessages]);
  useEffect(() => {
    if (!issue?.retryUntil) { const timer = window.setTimeout(() => setWait(0), 0); return () => window.clearTimeout(timer); }
    const update = () => setWait(Math.max(0, Math.ceil((issue.retryUntil! - Date.now()) / 1000)));
    const initial = window.setTimeout(update, 0); const timer = window.setInterval(update, 1000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [issue]);

  async function choose(cid: string) {
    if (busy) return;
    pending.current = null; oldestLoaded.current = null; ++messageRequest.current; selection.current = cid; setSelected(cid); setTurns([]); setPartial(""); setIssue(null);
    setTitle(conversations.find(c => c.conversation_id === cid)?.title || "");
    if (selected === cid) await loadMessages(cid).catch(error => setIssue(issueFor(error)));
  }
  async function create() {
    setLoading(true); setIssue(null);
    try {
      const conversation = await api<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ title: "New conversation" }) });
      await loadList(); await choose(conversation.conversation_id);
    } catch (error) { setIssue(issueFor(error)); }
    finally { setLoading(false); }
  }
  async function rename() {
    if (!selected || !title.trim()) return;
    setLoading(true);
    try { await api(`/conversations/${selected}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) }); await loadList(); }
    catch (error) { setIssue(issueFor(error)); }
    finally { setLoading(false); }
  }
  async function remove() {
    if (!selected || !window.confirm("Delete this conversation and its messages?")) return;
    setLoading(true);
    try { await api(`/conversations/${selected}`, { method: "DELETE" }); selection.current = null; setSelected(null); setTurns([]); pending.current = null; await loadList(); }
    catch (error) { setIssue(issueFor(error)); }
    finally { setLoading(false); }
  }
  async function stop() {
    const request = pending.current;
    controller.current?.abort("user");
    if (request) {
      try { await api(`/conversations/${request.cid}/generations/${request.client_request_id}/cancel`, { method: "POST" }); }
      catch (error) { setIssue(issueFor(error)); }
    }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    if (!selected || !question.trim() || busy || wait) return;
    const cid = selected;
    const attempt = pending.current?.cid === cid && pending.current.question === question.trim() ? pending.current : { cid, question: question.trim(), client_request_id: crypto.randomUUID() };
    pending.current = attempt;
    const request = new AbortController(); controller.current = request;
    const timer = window.setTimeout(() => request.abort("timeout"), 195000);
    let completed = false;
    setBusy(true); setIssue(null); setPartial("");
    try {
      await streamChat(`/conversations/${cid}/messages/stream`, JSON.stringify({ question: attempt.question, client_request_id: attempt.client_request_id }), request.signal, ({ event, data }) => {
        if (!mounted.current) return;
        if (event === "token" && typeof data.text === "string") setPartial(old => old + data.text);
        if (event === "completed") { completed = true; pending.current = null; }
        if (event === "error") {
          pending.current = null; setPartial("");
          setIssue({ message: String(data.message || "Answer could not complete"), code: String(data.code || "CHAT_UNAVAILABLE"), requestId: String(data.request_id || ""), retryUntil: data.retry_after ? Date.now() + Number(data.retry_after) * 1000 : 0 });
        }
      });
      if (completed) setQuestion("");
    } catch (error) {
      if (mounted.current) {
        setPartial("");
        if (request.signal.aborted) { pending.current = null; setIssue({ message: request.signal.reason === "user" ? "Stop requested. An answer completed before cancellation may still be saved." : "Generation timed out or the page changed. Reload the conversation to check its status." }); }
        else { const next = issueFor(error); if (["CHAT_REQUEST_FINISHED", "CHAT_REQUEST_CONFLICT"].includes(next.code || "")) pending.current = null; setIssue(next); }
      }
    } finally {
      window.clearTimeout(timer);
      if (mounted.current) {
        setBusy(false); setPartial("");
        await loadMessages(cid).catch(error => { setTurns([]); setIssue(issueFor(error)); });
      }
    }
  }
  async function source(citation: Citation) {
    try { await download(citation.source_path, citation.filename); }
    catch (error) { setTurns([]); setPartial(""); setIssue(issueFor(error)); if (selected) await loadMessages(selected).catch(() => {}); }
  }

  return <div className="chat-layout">
    <aside className="panel chat-list"><h1>{historyOnly ? "Chat history" : "Conversations"}</h1>
      {historyOnly ? <Link className="button primary" href="/chat">Open AI Chat</Link> : <button className="button primary" disabled={busy || loading} onClick={() => void create()}>New conversation</button>}
      {!conversations.length && <p>No conversations yet.</p>}
      {conversations.map(conversation => historyOnly ? <Link key={conversation.conversation_id} className="nav-link" href={`/chat?conversation=${conversation.conversation_id}`}>{conversation.title}</Link> : <button key={conversation.conversation_id} className={`button subtle ${selected === conversation.conversation_id ? "selected" : ""}`} disabled={busy} onClick={() => void choose(conversation.conversation_id)}>{conversation.title}</button>)}
      {listCursor && <button className="button subtle" disabled={loading || busy} onClick={() => void loadList(listCursor).catch(error => setIssue(issueFor(error)))}>Load more conversations</button>}
    </aside>
    <section className="panel chat-main">
      {issue && <div className="inline-error" role="alert"><p>{issue.message}</p>{issue.code && <p>Code: {issue.code}</p>}{issue.requestId && <p>Request ID: {issue.requestId}</p>}{wait > 0 && <p>Retry in {wait} seconds.</p>}</div>}
      {!historyOnly && !selected && <p>Create or select a conversation to ask questions about documents you can access.</p>}
      {selected && <>
        <div className="chat-toolbar"><label>Conversation title<input value={title} maxLength={100} onChange={event => setTitle(event.target.value)} /></label><button className="button subtle" disabled={busy || loading || !title.trim()} onClick={() => void rename()}>Rename</button><button className="button subtle" disabled={busy || loading} onClick={() => void remove()}>Delete conversation</button><button className="button subtle" disabled={busy} onClick={() => void loadMessages(selected).catch(error => setIssue(issueFor(error)))}>Reload messages</button></div>
        {messageCursor && <button className="button subtle" disabled={busy} onClick={() => void loadMessages(selected, messageCursor).catch(error => setIssue(issueFor(error)))}>Load older messages</button>}
        <div className="chat-messages" aria-busy={busy}>
          {turns.map(turn => <article key={turn.turn_id} className="chat-turn"><h2>You</h2><p className="passage-text">{turn.question}</p><h2>Assistant</h2>{turn.is_complete ? <><p className="passage-text">{turn.answer}</p><div className="document-actions">{turn.citations.map(citation => <button key={citation.source_id} className="button subtle small-button" onClick={() => void source(citation)}>{citation.source_id}: {citation.filename}{citation.page ? ` · Page ${citation.page}` : ""}</button>)}</div><AnswerFeedback turnId={turn.turn_id} saved={turn.feedback} onSaved={feedback => {
            // An in-flight history refresh may contain the previous vote.
            messageRequest.current++;
            setTurns(old => old.map(item => item.turn_id === turn.turn_id && item.is_complete ? { ...item, feedback } : item));
          }} /></> : <p>{turn.status === "HIDDEN" ? "Answer hidden because its source access or version changed." : `Answer incomplete (${turn.status.toLowerCase()}). You can submit another question.`}</p>}</article>)}
          {busy && <article className="chat-turn"><h2>Assistant — provisional</h2><p className="muted-note">Checking sources and generating. This text is unverified until the answer completes.</p><p className="passage-text">{partial || "Working…"}</p></article>}
        </div>
        <form className="document-upload-form" onSubmit={event => void send(event)}><label htmlFor="chat-question">Your question</label><textarea id="chat-question" rows={3} maxLength={2000} value={question} disabled={busy} onChange={event => setQuestion(event.target.value)} required /><p className="muted-note">Answers use authorized documents. If evidence is insufficient, the assistant will say so. Keep questions short.</p><div className="document-actions"><button className="button primary" disabled={busy || !question.trim() || wait > 0} type="submit">Send question</button>{busy && <button className="button subtle" type="button" onClick={() => void stop()}>Stop generation</button>}</div></form>
      </>}
    </section>
  </div>;
}
