import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function HomePage() {
  return <PlaceholderPage title="Home" description="The approved workspace shell is ready for the FastAPI integration." cards={[{ title: "AI Chat", description: "Placeholder. Streaming, RAG, citations, and authentication are not implemented." }, { title: "Documents", description: "Placeholder. Uploads, authorization, and ingestion are not implemented." }, { title: "Admin", description: "Placeholder. User management and dashboards are not implemented." }]} />;
}
