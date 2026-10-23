import { PlaceholderPage } from "../../src/components/placeholder-page";

export default function SettingsPage() {
  return <PlaceholderPage title="Settings" description="This route is reserved for profile and workspace preferences." cards={[{ title: "Profile", description: "Placeholder. Admin-created accounts and password/JWT authentication are not implemented." }, { title: "Department", description: "Placeholder for the seeded department context." }, { title: "Preferences", description: "Placeholder for future assistant preferences." }]} />;
}
