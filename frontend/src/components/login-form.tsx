"use client";

import { useState, type FormEvent } from "react";
import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "../lib/auth-context";

export function LoginFormContent() {
  const { login, status, user, message } = useAuth();
  const router = useRouter();
  const search = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = search.get("next");
  const destination = next && next.startsWith("/") && !next.startsWith("//") && !next.includes("\\") ? next : "/home";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null); setBusy(true);
    try { await login(email, password); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Sign in failed. Check your credentials and retry."); }
    finally { setBusy(false); }
  }
  useEffect(() => { if (user && status === "authenticated") router.replace(destination); }, [destination, router, status, user]);
  return <main className="login-page"><section className="login-card">
    <div className="login-brand"><span className="brand-mark">E</span><span>Enterprise AI Assistant</span></div>
    <p className="eyebrow">SECURE WORKSPACE</p><h1>Welcome back</h1><p className="login-intro">Sign in with the account provided by your organization administrator.</p>
    {message && <p className="inline-warning" role="status">{message}</p>}
    <form onSubmit={submit} className="form-stack">
      <label htmlFor="email">Work email</label><input id="email" type="email" autoComplete="username" required maxLength={254} value={email} onChange={(event) => setEmail(event.target.value)} />
      <label htmlFor="password">Password</label><input id="password" type="password" autoComplete="current-password" required maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} />
      {error && <p className="inline-error" role="alert">{error}</p>}
      <button className="button primary full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
    <p className="login-footnote">Accounts are created by an administrator. Contact your administrator if you need access.</p>
  </section><footer className="login-footer">Enterprise AI Assistant · Your organization’s private workspace</footer></main>;
}
