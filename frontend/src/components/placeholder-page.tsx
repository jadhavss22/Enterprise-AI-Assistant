import type { Role } from "../lib/auth-context";
import { PlaceholderShell } from "./placeholder-shell";

export function PlaceholderPage({ title, description, cards, roles }: { title: string; description: string; cards: readonly { title: string; description: string }[]; roles?: Role[] }) {
  return <PlaceholderShell title={title} roles={roles}><div className="page-wrap"><div className="page-heading"><p className="eyebrow">PLANNED EXPERIENCE</p><h1>{title}</h1><p>{description}</p><span className="planned-label">NOT IMPLEMENTED YET</span></div><div className="home-grid">{cards.map((card) => <section className="feature-card planned" key={card.title}><span className="feature-icon">◇</span><h3>{card.title}</h3><p>{card.description}</p></section>)}</div></div></PlaceholderShell>;
}
