"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useState } from "react";
import { AuthGate } from "./auth-gate";
import { useAuth, type Role } from "../lib/auth-context";

const navigation: { label: string; href: string; roles?: Role[] }[] = [
  { label: "Home", href: "/home" }, { label: "AI Chat", href: "/chat" },
  { label: "Documents", href: "/documents" }, { label: "Chat History", href: "/history" },
  { label: "Settings", href: "/settings" }, { label: "Manager uploads", href: "/manager/upload", roles: ["manager", "admin"] },
  { label: "Admin workspace", href: "/admin", roles: ["admin"] }, { label: "User management", href: "/admin/users", roles: ["admin"] },
  { label: "Ingestion jobs", href: "/admin/ingestion-jobs", roles: ["admin"] },
];

export function PlaceholderShell({ title, children, roles }: { title: string; children: ReactNode; roles?: Role[] }) {
  return <AuthGate roles={roles}><Workspace title={title}>{children}</Workspace></AuthGate>;
}

function Workspace({ title, children }: { title: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function signOut() {
    setBusy(true); setError(null);
    try { await logout(); router.replace("/login"); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Sign out failed. Retry."); }
    finally { setBusy(false); }
  }
  return <main className="shell">
    <aside className="sidebar"><Link href="/home" className="brand"><span className="brand-mark">E</span><span>Enterprise AI<small>ASSISTANT WORKSPACE</small></span></Link>
      <p className="nav-caption">WORKSPACE</p><nav aria-label="Primary navigation">{navigation.filter((item) => !item.roles || item.roles.includes(user!.role)).map((item) => <Link key={item.href} href={item.href} className={`nav-link ${pathname === item.href ? "active" : ""}`}>{item.label}</Link>)}</nav>
      <div className="sidebar-bottom"><span className="online-dot"/> Connected to your organization</div>
    </aside>
    <section className="content"><header className="header"><div><span className="breadcrumb">WORKSPACE</span><span className="breadcrumb-separator">/</span><strong>{title}</strong></div><div className="account-area"><div className="avatar">{user!.display_name.slice(0, 1).toUpperCase()}</div><div className="account-copy"><strong>{user!.display_name}</strong><span>{user!.role} · {user!.email}</span></div><button className="button subtle" type="button" onClick={() => void signOut()} disabled={busy}>{busy ? "Signing out…" : "Sign out"}</button></div></header>
      {error && <p className="inline-error" role="alert">{error}</p>}{children}
    </section>
  </main>;
}
