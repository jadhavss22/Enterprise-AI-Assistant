import { PlaceholderShell } from "./placeholder-shell";

export function PlaceholderPage({ title, description, cards }: { title: string; description: string; cards: readonly { title: string; description: string }[] }) {
  return (
    <PlaceholderShell title={title}>
      <div className="hero">
        <p className="eyebrow">PLACEHOLDER SCREEN</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="grid">
        {cards.map((card) => (
          <section key={card.title}>
            <h2>{card.title}</h2>
            <p>{card.description}</p>
          </section>
        ))}
      </div>
    </PlaceholderShell>
  );
}
