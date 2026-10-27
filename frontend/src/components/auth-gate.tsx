"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useAuth, type Role } from "../lib/auth-context";

export function AuthGate({ children, roles }: { children: ReactNode; roles?: Role[] }) {
  const { user, status, message, refresh } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  useEffect(() => {
    if (status === "anonymous") router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [pathname, router, status]);
  if (status === "loading") return <main className="gate"><p role="status">Checking your session…</p></main>;
  if (status === "unavailable") return <main className="gate"><section className="panel"><p className="eyebrow">CONNECTION ISSUE</p><h1>We couldn’t restore your session</h1><p>{message}</p><button className="button primary" onClick={() => { void refresh().catch(() => undefined); }}>Retry</button></section></main>;
  if (status === "anonymous" || !user) return <main className="gate"><p role="status">Taking you to sign in…</p></main>;
  if (roles && !roles.includes(user.role)) return <main className="gate"><section className="panel"><p className="eyebrow">ACCESS RESTRICTED</p><h1>You don’t have access to this page</h1><p>Your current role does not allow this action. The API also enforces this permission.</p><Link className="button primary" href="/home">Return to workspace</Link></section></main>;
  return children;
}
