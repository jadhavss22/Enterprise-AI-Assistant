import { AdminDashboard } from "../../src/components/admin-dashboard";
import { PlaceholderShell } from "../../src/components/placeholder-shell";

export default function AdminPage() {
  return <PlaceholderShell title="Admin Workspace" roles={["admin"]}>
    <AdminDashboard />
  </PlaceholderShell>;
}
