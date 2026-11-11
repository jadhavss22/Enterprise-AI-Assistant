"use client";

import { useRef, useState } from "react";
import { useAuth } from "../lib/auth-context";

type Rating = "THUMBS_UP" | "THUMBS_DOWN";
export type Feedback = { rating: Rating; comment: string | null };

export function AnswerFeedback({ turnId, saved, onSaved }: {
  turnId: string;
  saved?: Feedback | null;
  onSaved: (value: Feedback) => void;
}) {
  const { api } = useAuth();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<Feedback | null>(null);
  const sending = useRef(false);

  async function save(value: Feedback) {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setFailed(null);
    try {
      const result = await api<Feedback>(`/messages/${turnId}/feedback`, {
        method: "POST",
        body: JSON.stringify(value),
      });
      onSaved(result);
      setEditing(value.rating === "THUMBS_DOWN" && !value.comment);
      setDraft(result.comment ?? "");
    } catch {
      setFailed(value);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  function downvote() {
    setDraft(saved?.comment ?? "");
    setEditing(true);
    if (saved?.rating !== "THUMBS_DOWN") void save({ rating: "THUMBS_DOWN", comment: null });
  }

  return <section className="answer-feedback" aria-label="Answer feedback">
    <p>Was this answer helpful?</p>
    <div className="document-actions">
      <button type="button" className={`button small-button ${saved?.rating === "THUMBS_UP" ? "" : "subtle"}`}
        aria-label="Helpful" title="Helpful" aria-pressed={saved?.rating === "THUMBS_UP"}
        disabled={busy} onClick={() => void save({ rating: "THUMBS_UP", comment: null })}>👍</button>
      <button type="button" className={`button small-button ${saved?.rating === "THUMBS_DOWN" ? "" : "subtle"}`}
        aria-label="Unhelpful" title="Unhelpful" aria-pressed={saved?.rating === "THUMBS_DOWN"}
        disabled={busy} onClick={downvote}>👎</button>
      {saved?.rating === "THUMBS_DOWN" && !editing && <button type="button"
        className="button subtle small-button" disabled={busy} onClick={downvote}>
        {saved.comment ? "Edit feedback" : "Add a comment"}
      </button>}
    </div>
    {editing && <form onSubmit={event => {
      event.preventDefault();
      void save({ rating: "THUMBS_DOWN", comment: draft.trim() || null });
    }}>
      <label htmlFor={`feedback-${turnId}`}>What were you looking for? (optional)</label>
      <textarea id={`feedback-${turnId}`} rows={3} maxLength={1000} value={draft}
        placeholder="Tell us what was missing or unclear."
        disabled={busy} onChange={event => setDraft(event.target.value)} />
      <div className="document-actions">
        <button className="button small-button" disabled={busy}>Save comment</button>
        <button type="button" className="button subtle small-button" disabled={busy}
          onClick={() => setEditing(false)}>Close</button>
      </div>
    </form>}
    {failed && <div role="alert">
      Feedback could not be saved.{" "}
      <button type="button" className="button subtle small-button"
        onClick={() => void save(failed)}>Retry</button>
    </div>}
  </section>;
}
