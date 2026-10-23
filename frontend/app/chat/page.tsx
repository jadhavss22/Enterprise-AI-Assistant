import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function ChatPage() {
  return <PlaceholderPage title="AI Chat" description="This route is reserved for conversational search and source inspection." cards={[{ title: "Conversation area", description: "Placeholder for chat messages and the composer." }, { title: "Sources", description: "Placeholder for validated citations and source access checks." }, { title: "Streaming", description: "Placeholder for authenticated FastAPI streaming responses." }]} />;
}
