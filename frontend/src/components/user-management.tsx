"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { PlaceholderShell } from "./placeholder-shell";
import { useAuth, type ApiError, type User } from "../lib/auth-context";

type Department = { department_id: string; code: string; name: string };
type UserPage = { users: User[]; next_cursor: string | null };
type Draft = { email: string; display_name: string; password: string; role: "EMPLOYEE" | "MANAGER" | "ADMIN"; department_id: string };
const blank: Draft = { email: "", display_name: "", password: "", role: "EMPLOYEE", department_id: "" };

export function UserManagement() {
  const { api, refresh, user: currentUser } = useAuth();
  const [rows, setRows] = useState<User[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(blank);
  const [editing, setEditing] = useState<User | null>(null);
  const [editName, setEditName] = useState("");
  const [editRole, setEditRole] = useState<"EMPLOYEE" | "MANAGER" | "ADMIN">("EMPLOYEE");
  const [editStatus, setEditStatus] = useState<"ACTIVE" | "DISABLED">("ACTIVE");
  const [editDepartment, setEditDepartment] = useState("");

  const load = useCallback(async (nextCursor: string | null, role: string, status: string, signal?: AbortSignal) => {
    setLoading(true); setError(null);
    try {
      const query = new URLSearchParams({ limit: "20" });
      if (nextCursor) query.set("cursor", nextCursor);
      if (role) query.set("role", role);
      if (status) query.set("status", status);
      const page = await api<UserPage>(`/admin/users?${query}`, { signal });
      setRows(page.users); setCursor(page.next_cursor);
    } catch (cause) { if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(toMessage(cause)); }
    finally { setLoading(false); }
  }, [api]);

  useEffect(() => {
    const controller = new AbortController();
    void api<{ departments: Department[] }>("/admin/departments", { signal: controller.signal }).then((result) => setDepartments(result.departments)).catch((cause) => { if (!(cause instanceof DOMException && cause.name === "AbortError")) setError(toMessage(cause)); });
    return () => controller.abort();
  }, [api]);
  useEffect(() => { const controller = new AbortController(); void Promise.resolve().then(() => load(cursorStack[cursorStack.length - 1], roleFilter, statusFilter, controller.signal)); return () => controller.abort(); }, [cursorStack, load, roleFilter, statusFilter]);

  async function create(event: FormEvent) {
    event.preventDefault(); setSaving(true); setError(null); setNotice(null);
    try { await api<User>("/admin/users", { method: "POST", body: JSON.stringify(draft) }); setNotice("Account created. Share its initial password with the user through a secure channel."); setCreateOpen(false); setDraft(blank); setCursorStack([null]); }
    catch (cause) { setError(toMessage(cause)); }
    finally { setSaving(false); }
  }
  function startEdit(row: User) {
    setEditing(row); setEditName(row.display_name); setEditRole(row.role.toUpperCase() as Draft["role"]); setEditStatus(row.status.toUpperCase() as "ACTIVE" | "DISABLED"); setEditDepartment(row.department_id); setError(null); setNotice(null);
  }
  async function update(event: FormEvent) {
    event.preventDefault(); if (!editing) return;
    const body = { display_name: editName, role: editRole, status: editStatus, department_id: editDepartment };
    const selfSecurityChange = editing.user_id === currentUser?.user_id && (body.role.toLowerCase() !== editing.role || body.status !== editing.status.toUpperCase() || body.department_id !== editing.department_id);
    if (selfSecurityChange) {
      if (!window.confirm("This change revokes your current sessions and signs you out. Continue?")) return;
    } else if (body.status === "DISABLED" && !window.confirm("Disable this account? It will lose access immediately.")) return;
    setSaving(true); setError(null); setNotice(null);
    try {
      await api<User>(`/admin/users/${encodeURIComponent(editing.user_id)}`, { method: "PATCH", body: JSON.stringify(body) });
      const changesCurrentSecurity = editing.user_id === currentUser?.user_id;
      setEditing(null); setNotice("Account updated."); setCursorStack([null]);
      if (changesCurrentSecurity) await refresh();
    } catch (cause) { setError(toMessage(cause)); }
    finally { setSaving(false); }
  }
  const previous = cursorStack.length > 1;
  return <PlaceholderShell title="User Management" roles={["admin"]}><div className="page-wrap admin-users">
    <div className="page-heading admin-heading"><div><p className="eyebrow">ADMINISTRATION</p><h1>User management</h1><p>Create accounts, assign organization roles, and manage access.</p></div><button className="button primary" onClick={() => { setCreateOpen(true); setError(null); setNotice(null); }}>＋ Create user</button></div>
    <div className="user-toolbar panel"><div className="filter-row"><label>Role<select value={roleFilter} onChange={(event) => { setRoleFilter(event.target.value); setCursorStack([null]); }}><option value="">All roles</option><option value="ADMIN">Admin</option><option value="MANAGER">Manager</option><option value="EMPLOYEE">Employee</option></select></label><label>Status<select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setCursorStack([null]); }}><option value="">All statuses</option><option value="ACTIVE">Active</option><option value="DISABLED">Disabled</option></select></label></div><span className="muted-note">Accounts are created by an administrator</span></div>
    {error && <p className="inline-error" role="alert">{error}</p>}{notice && <p className="inline-success" role="status">{notice}</p>}
    <div className="table-wrap panel"><table><thead><tr><th>Person</th><th>Role</th><th>Department</th><th>Status</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
      {loading ? <tr><td colSpan={5} className="table-message">Loading accounts…</td></tr> : rows.length === 0 ? <tr><td colSpan={5} className="table-message">No users match these filters.</td></tr> : rows.map((row) => <tr key={row.user_id}><td><div className="person-cell"><span className="avatar small">{row.display_name.slice(0, 1).toUpperCase()}</span><span><strong>{row.display_name}</strong><small>{row.email}</small></span></div></td><td><span className={`role-pill ${row.role}`}>{row.role}</span></td><td>{departments.find((d) => d.department_id === row.department_id)?.name ?? "Department"}</td><td><span className={`status-pill ${row.status}`}>{row.status}</span></td><td><button className="button subtle small-button" onClick={() => startEdit(row)}>Manage</button></td></tr>)}
    </tbody></table><div className="table-footer"><span>Showing up to 20 accounts</span><div className="button-row"><button className="button subtle" disabled={!previous || loading} onClick={() => setCursorStack((stack) => stack.slice(0, -1))}>Previous</button><button className="button subtle" disabled={!cursor || loading} onClick={() => cursor && setCursorStack((stack) => [...stack, cursor])}>Next</button></div></div></div>
    <p className="muted-note">Password reset, department editing, session inspection, and audit browsing are outside this milestone.</p>

    {(createOpen || editing) && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) { setCreateOpen(false); setEditing(null); } }}><section className="modal panel" role="dialog" aria-modal="true" aria-labelledby="user-form-title"><div className="modal-heading"><div><p className="eyebrow">ACCOUNT ACCESS</p><h2 id="user-form-title">{editing ? "Manage account" : "Create user"}</h2></div><button className="icon-button" aria-label="Close" onClick={() => { setCreateOpen(false); setEditing(null); }}>×</button></div>
      <form className="form-stack" onSubmit={editing ? update : create}>
        {editing ? <><label htmlFor="edit-name">Full name</label><input id="edit-name" required maxLength={120} value={editName} onChange={(e) => setEditName(e.target.value)} /><div className="form-columns"><div><label htmlFor="edit-role">Role</label><select id="edit-role" value={editRole} onChange={(e) => setEditRole(e.target.value as Draft["role"])}><option value="EMPLOYEE">Employee</option><option value="MANAGER">Manager</option><option value="ADMIN">Admin</option></select></div><div><label htmlFor="edit-status">Status</label><select id="edit-status" value={editStatus} onChange={(e) => setEditStatus(e.target.value as "ACTIVE" | "DISABLED")}><option value="ACTIVE">Active</option><option value="DISABLED">Disabled</option></select></div></div><label htmlFor="edit-department">Department</label><select id="edit-department" required value={editDepartment} onChange={(e) => setEditDepartment(e.target.value)}>{departments.map((d) => <option key={d.department_id} value={d.department_id}>{d.name}</option>)}</select><p className="muted-note">Changing role, status, or department revokes existing sessions.</p></> : <><label htmlFor="new-name">Full name</label><input id="new-name" required maxLength={120} value={draft.display_name} onChange={(e) => setDraft({ ...draft, display_name: e.target.value })} /><label htmlFor="new-email">Work email</label><input id="new-email" type="email" required maxLength={254} autoComplete="off" value={draft.email} onChange={(e) => setDraft({ ...draft, email: e.target.value })} /><label htmlFor="new-password">Temporary password</label><input id="new-password" type="password" required minLength={15} maxLength={128} autoComplete="new-password" value={draft.password} onChange={(e) => setDraft({ ...draft, password: e.target.value })} /><div className="form-columns"><div><label htmlFor="new-role">Role</label><select id="new-role" value={draft.role} onChange={(e) => setDraft({ ...draft, role: e.target.value as Draft["role"] })}><option value="EMPLOYEE">Employee</option><option value="MANAGER">Manager</option><option value="ADMIN">Admin</option></select></div><div><label htmlFor="new-department">Department</label><select id="new-department" required value={draft.department_id} onChange={(e) => setDraft({ ...draft, department_id: e.target.value })}><option value="">Select…</option>{departments.map((d) => <option key={d.department_id} value={d.department_id}>{d.name}</option>)}</select></div></div><p className="muted-note">The password is used to create this account. Share it with the user over an approved secure channel.</p></>}
        {error && <p className="inline-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="button subtle" disabled={saving} onClick={() => { setCreateOpen(false); setEditing(null); }}>Cancel</button><button className="button primary" disabled={saving || departments.length === 0}>{saving ? "Saving…" : editing ? "Save changes" : "Create account"}</button></div>
      </form></section></div>}
  </div></PlaceholderShell>;
}

function toMessage(cause: unknown): string {
  const error = cause as ApiError;
  if (error?.code === "LAST_ACTIVE_ADMIN") return "This is the last active Admin. Create another active Admin before changing this account.";
  if (error?.code === "EMAIL_EXISTS") return "An account already uses this email address.";
  if (error?.status === 429) return `Too many requests. Retry after ${error.retryAfter ?? "the indicated wait"}.`;
  if (error?.status === 503) return "The service is unavailable. Check the local API and data services, then retry.";
  return error?.message ?? "The request failed. Review the information and retry.";
}
