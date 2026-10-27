"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { PlaceholderShell } from "../../src/components/placeholder-shell";
import { useAuth } from "../../src/lib/auth-context";

export default function SettingsPage() {
  const { user, logout, logoutAll } = useAuth();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function endSessions(all: boolean) {
    setBusy(true); setError(null);
    try { if (all) await logoutAll(); else await logout(); router.replace("/login"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "The session could not be revoked. Please retry."); }
    finally { setBusy(false); }
  }
  return <PlaceholderShell title="Settings"><div className="page-wrap"><div className="page-heading"><p className="eyebrow">ACCOUNT</p><h1>Profile and security</h1><p>Review your current organization profile and manage active sign-in sessions.</p></div>
    <div className="settings-grid"><section className="panel"><h2>Profile</h2><dl className="profile-list"><div><dt>Name</dt><dd>{user?.display_name}</dd></div><div><dt>Email</dt><dd>{user?.email}</dd></div><div><dt>Role</dt><dd><span className="role-pill">{user?.role}</span></dd></div><div><dt>Department ID</dt><dd className="mono">{user?.department_id}</dd></div></dl></section>
    <section className="panel"><h2>Sessions</h2><p>Sign out here, or revoke every active session for this account. Revoking all sessions signs you out on this device too.</p>{error && <p className="inline-error" role="alert">{error}</p>}<div className="button-row"><button className="button subtle" disabled={busy} onClick={() => void endSessions(false)}>Sign out</button><button className="button danger" disabled={busy} onClick={() => void endSessions(true)}>{busy ? "Revoking…" : "Sign out everywhere"}</button></div></section></div>
    <p className="muted-note">Password changes and personal preferences are not available in this milestone.</p></div></PlaceholderShell>;
}
