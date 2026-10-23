import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function AdminPage() {
  return <PlaceholderPage title="Admin Workspace" description="Organization-wide publishing and administration are reserved for the Admin role." cards={[{ title: "Users", description: "Placeholder for Admin-created accounts." }, { title: "Documents", description: "Placeholder for audited organization-wide document controls." }, { title: "Audit", description: "Placeholder for access and publishing audit records." }]} />;
}
