import { PlaceholderPage } from "../../../src/components/placeholder-page";

export default function ManagerUploadPage() { return <PlaceholderPage title="Manager Upload" description="Managers will manage their department uploads in a later slice." roles={["manager", "admin"]} cards={[{ title: "Supported files", description: "Planned: PDF, DOCX, and TXT uploads." }, { title: "Department scope", description: "Planned: access limited by the backend to approved department members." }, { title: "Processing", description: "Planned: named processing stages and status." }]} />; }
