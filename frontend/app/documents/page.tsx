import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function DocumentsPage() {
  return <PlaceholderPage title="Documents" description="This route is reserved for accessible documents and saved-answer visibility." cards={[{ title: "Document library", description: "Placeholder for department-scoped documents." }, { title: "Processing stages", description: "Placeholder for named ingestion stages, without percentages." }, { title: "Access state", description: "Placeholder for hiding answers whose source documents become inaccessible." }]} />;
}
