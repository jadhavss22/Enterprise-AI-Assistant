"use client";

import Link from "next/link";
import { useAuth } from "../../src/lib/auth-context";
import { PlaceholderShell } from "../../src/components/placeholder-shell";

export default function HomePage() {
  const { user } = useAuth();
  return <PlaceholderShell title="Home"><div className="page-wrap"><div className="welcome-banner"><div><p className="eyebrow">YOUR ORGANIZATION WORKSPACE</p><h1>Welcome, {user?.display_name.split(" ")[0]}.</h1><p>Your secure workspace is ready. Chat and document experiences will be added in upcoming project slices.</p><Link className="button primary" href="/chat">Open AI chat <span aria-hidden="true">→</span></Link></div><div className="welcome-art" aria-hidden="true"><div className="orb orb-one"/><div className="orb orb-two"/><div className="orb-center">✳</div></div></div><div className="section-heading"><div><h2>Workspace</h2><p>Start with the areas available to your account.</p></div></div><div className="home-grid"><Link href="/chat" className="feature-card"><span className="feature-icon">✦</span><h3>AI Chat</h3><p>Ask questions and explore answers with sources.</p><span className="card-link">Planned for a later phase →</span></Link><Link href="/documents" className="feature-card"><span className="feature-icon">▤</span><h3>Documents</h3><p>Browse documents you are allowed to access.</p><span className="card-link">Planned for a later phase →</span></Link><Link href="/history" className="feature-card"><span className="feature-icon">◷</span><h3>Chat history</h3><p>Return to your previous conversations.</p><span className="card-link">Planned for a later phase →</span></Link></div></div></PlaceholderShell>;
}
