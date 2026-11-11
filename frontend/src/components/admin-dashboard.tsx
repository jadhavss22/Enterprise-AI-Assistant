"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth, type ApiError } from "../lib/auth-context";

type Summary = {
  checked_at: string;
  documents: { total: number; by_status: Record<string, number> } | null;
  failed_jobs: number | null;
  users: { active_by_role: Record<string, number>; disabled: number } | null;
  chat: { conversations: number; questions: number; completed: number } | null;
  feedback: { THUMBS_UP: number; THUMBS_DOWN: number } | null;
  health: { api: string; worker: string };
};

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="feature-card">
    <h2>{title}</h2>
    {children ?? <p>Unavailable</p>}
  </section>;
}

function Breakdown({ values, table, tone = "blue" }: {
  values?: Record<string, number>;
  table: boolean;
  tone?: "blue" | "green" | "violet";
}) {
  if (!values) return <p className="dash-muted">Unavailable</p>;
  const rows = Object.entries(values);
  const max = Math.max(1, ...rows.map(([, count]) => count));
  if (table) return <table className="dash-table">
    <thead><tr><th scope="col">Metric</th><th scope="col">Count</th></tr></thead>
    <tbody>{rows.map(([label, count]) => <tr key={label}>
      <th scope="row">{label}</th><td>{count.toLocaleString()}</td>
    </tr>)}</tbody>
  </table>;
  return <dl className={`dash-bars dash-${tone}`}>{rows.map(([label, count]) => (
    <div key={label}>
      <dt>{label}</dt><dd>{count.toLocaleString()}</dd>
      <div className="dash-track" aria-hidden="true">
        <span style={{ width: `${count / max * 100}%` }} />
      </div>
    </div>
  ))}</dl>;
}

function Metric({ label, value, detail }: {
  label: string; value?: number | null; detail: string;
}) {
  return <div className="dash-metric">
    <span>{label}</span>
    <strong>{value == null ? "—" : value.toLocaleString()}</strong>
    <small>{value == null ? "Unavailable" : detail}</small>
  </div>;
}

function Health({ label, state }: { label: string; state: string }) {
  return <li><span>{label}</span><span className="dash-pill" data-state={state}>{state}</span></li>;
}

export function AdminDashboard() {
  const { api } = useAuth();
  const [table, setTable] = useState(false);
  const [days, setDays] = useState("7");
  const [revision, refresh] = useState(0);
  const [result, setResult] = useState<{
    key: string; data?: Summary; error?: string; unreachable?: boolean;
  }>();
  const key = `${days}:${revision}`;
  const current = result?.key === key ? result : undefined;
  const data = current?.data;
  const loading = !current;

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 20000);
    let active = true;
    void api<Summary>(`/admin/dashboard?days=${days}`, { signal: controller.signal })
      .then(data => { if (active) setResult({ key, data }); })
      .catch((error: ApiError) => {
        if (active) setResult({
          key,
          error: error.status === 403 ? "Access denied." : "Dashboard could not be loaded.",
          unreachable: !error.status,
        });
      })
      .finally(() => window.clearTimeout(timer));
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [api, days, key]);

  const roles = data?.users?.active_by_role;
  const activeUsers = roles ? Object.values(roles).reduce((sum, count) => sum + count, 0) : null;
  const ratings = data?.feedback;
  const voteCount = ratings ? ratings.THUMBS_UP + ratings.THUMBS_DOWN : null;
  const documentStatuses = data?.documents && Object.fromEntries(
    Object.entries(data.documents.by_status).map(([status, count]) => [
      status.toLowerCase().replaceAll("_", " ").replace(/^./, char => char.toUpperCase()), count,
    ]),
  );

  return <div className="page-wrap dash">
    <header className="dash-header">
      <div>
        <p className="eyebrow">ADMINISTRATION</p>
        <h1>Admin dashboard</h1>
        <p>Workspace health, adoption, and answer feedback.</p>
      </div>
      <button type="button" className="button subtle small-button"
        disabled={loading} onClick={() => refresh(value => value + 1)}>Refresh</button>
    </header>
    <div className="dash-toolbar">
      <fieldset><legend>Activity window</legend>
        {["7", "30"].map(window => <button key={window} type="button"
          className="dash-toggle" aria-pressed={days === window}
          onClick={() => setDays(window)}>Last {window} days</button>)}
      </fieldset>
      <fieldset><legend>Display</legend>
        <button type="button" className="dash-toggle" aria-pressed={!table}
          onClick={() => setTable(false)}>Charts</button>
        <button type="button" className="dash-toggle" aria-pressed={table}
          onClick={() => setTable(true)}>Tables</button>
      </fieldset>
    </div>
    <p className="dash-muted" role="status">{loading ? "Loading dashboard…" : data
      ? `Last checked ${new Date(data.checked_at).toLocaleString()}`
      : current?.error}</p>
    {!loading && <>
      <section className="dash-metrics" aria-label="Key metrics">
        <Metric label="Documents" value={data?.documents?.total} detail="Current snapshot" />
        <Metric label="Active users" value={activeUsers} detail="Current snapshot" />
        <Metric label="Questions" value={data?.chat?.questions} detail={`Last ${days} days`} />
        <Metric label="Ratings" value={voteCount} detail={`Last ${days} days`} />
      </section>
      <div className="dash-layout">
        <div className="dash-panels">
          <Panel title="Chat usage">
            <Breakdown table={table} values={data?.chat ? {
              "Conversations created": data.chat.conversations,
              "Questions submitted": data.chat.questions,
              "Completed answers": data.chat.completed,
            } : undefined} />
            <p className="dash-muted">Counts reflect retained records. Deleting a chat can reduce totals.</p>
          </Panel>
          <Panel title="Answer feedback">
            <Breakdown table={table} tone="green" values={ratings ? {
              Helpful: ratings.THUMBS_UP, Unhelpful: ratings.THUMBS_DOWN,
            } : undefined} />
            {voteCount === 0 && <p className="dash-muted">No ratings in this window.</p>}
            <p className="dash-muted">Latest saved votes, grouped by the date first rated.</p>
          </Panel>
          <Panel title="Document status">
            {documentStatuses && Object.keys(documentStatuses).length === 0
              ? <p>No documents yet.</p>
              : <Breakdown table={table} values={documentStatuses ?? undefined} />}
          </Panel>
          <Panel title="Users by role">
            <Breakdown table={table} tone="violet" values={roles && data?.users ? {
              Employee: roles.EMPLOYEE, Manager: roles.MANAGER,
              Admin: roles.ADMIN, Disabled: data.users.disabled,
            } : undefined} />
          </Panel>
        </div>
        <aside className="dash-sidebar">
          <Panel title="Service health">
            <ul className="dash-health">
              <Health label="API" state={data?.health.api ?? (current?.unreachable ? "unavailable" : "unknown")} />
              <Health label="Worker" state={data?.health.worker ?? "unknown"} />
            </ul>
            <p className="dash-muted">Worker status uses the embedding heartbeat.</p>
          </Panel>
          <Panel title="Ingestion">
            <Metric label="Failed jobs" value={data?.failed_jobs} detail={`Last ${days} days`} />
            <Link className="dash-link" href="/admin/ingestion-jobs">Review ingestion jobs →</Link>
          </Panel>
          <Panel title="Shortcuts">
            <Link className="dash-link" href="/admin/users">Manage users →</Link>
            <Link className="dash-link" href="/documents">Open document library →</Link>
          </Panel>
        </aside>
      </div>
    </>}
  </div>;
}
