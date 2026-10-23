"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useAuth } from "../lib/auth-context";

const navigation = [
  ["Home", "/home"],
  ["AI Chat", "/chat"],
  ["Documents", "/documents"],
  ["Chat History", "/history"],
  ["Settings", "/settings"],
] as const;

export function PlaceholderShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, loading, logout } = useAuth();
  const role = user?.role;
  return (
    <main className="shell">
      <nav className="sidebar" aria-label="Primary navigation">
        <div className="brand">Enterprise AI Assistant</div>
        <p className="badge">Frontend scaffold</p>
        {navigation.map(([label, href]) => (
          <Link key={href} href={href} className={href === "/" + title.toLowerCase() ? "active" : ""}>
            {label}
          </Link>
        ))}
        {(role === "manager" || role === "admin") && <><div className="sidebar-divider" /><Link href="/manager/upload">Manager upload</Link></>}
        {role === "admin" && <><Link href="/admin">Admin workspace</Link><Link href="/admin/users">User management</Link></>}
      </nav>
      <section className="content">
        <header className="header">
          <span>WORKSPACE / {title}</span>
          <span className="status">{loading ? "Restoring session" : user ? `${user.display_name} (${user.role})` : "Signed out"}</span>
        </header>
        {user && <button type="button" onClick={() => void logout()}>Sign out</button>}
        {children}
      </section>
    </main>
  );
}
