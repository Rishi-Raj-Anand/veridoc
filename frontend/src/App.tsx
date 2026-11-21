/**
 * Application shell and routes (CLAUDE.md §10).
 *
 * Route guards by role and the real pages arrive in Phase 6; Phase 0 only proves
 * the shell renders and the service chain is reachable.
 */

import { Navigate, Route, Routes } from "react-router";

import { HealthBanner } from "./components/HealthBanner";
import { AdminPage } from "./pages/AdminPage";
import { ChatPage } from "./pages/ChatPage";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white px-6 py-4">
        <h1 className="text-2xl font-bold">VeriDoc AI</h1>
        <HealthBanner />
      </header>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/admin" element={<AdminPage />} />
          <Route path="*" element={<p className="text-slate-600">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
