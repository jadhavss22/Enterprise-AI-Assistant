import { AuthGate } from "../../../src/components/auth-gate";
import { UserManagement } from "../../../src/components/user-management";

export default function AdminUsersPage() { return <AuthGate roles={["admin"]}><UserManagement /></AuthGate>; }
