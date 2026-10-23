import { PlaceholderPage } from "../../../src/components/placeholder-page";

export default function AdminUsersPage() {
  return (
    <PlaceholderPage
      title="User Management"
      description="Admin-created accounts and role assignment will be implemented in the authentication slice."
      cards={[
        { title: "Accounts", description: "Placeholder for creating and disabling user accounts." },
        { title: "Roles", description: "Placeholder for Admin, Manager, and Employee role assignment." },
        { title: "Audit", description: "Placeholder for audited account and access changes." },
      ]}
    />
  );
}
