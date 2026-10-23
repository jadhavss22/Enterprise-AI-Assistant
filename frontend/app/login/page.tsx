"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../../src/lib/auth-context";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(null);
    try { await login(email, password); router.push("/home"); }
    catch { setError("Sign-in failed. Check your credentials and try again."); }
  }

  return <main className="shell"><section className="content"><h1>Sign in</h1><p>Use an Admin-created account.</p><form onSubmit={submit} style={{ display: "grid", gap: "1rem", maxWidth: 420 }}>
    <label>Email<input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required /></label>
    <label>Password<input value={password} onChange={(event) => setPassword(event.target.value)} type="password" required /></label>
    {error && <p role="alert">{error}</p>}<button type="submit">Sign in</button>
  </form></section></main>;
}
