import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import ChatPage from "./pages/chat/ChatPage";
import KnowledgePage from "./pages/knowledge/KnowledgePage";
import SettingsPage from "./pages/settings/SettingsPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
