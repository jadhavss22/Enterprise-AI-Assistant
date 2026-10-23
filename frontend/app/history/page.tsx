import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function HistoryPage() {
  return <PlaceholderPage title="Chat History" description="This route is reserved for saved conversations and answer access rechecks." cards={[{ title: "Recent conversations", description: "Placeholder for user-visible chat history." }, { title: "Saved answers", description: "Placeholder for answers backed by currently accessible sources." }, { title: "Search", description: "Placeholder for history filtering and retrieval." }]} />;
}
