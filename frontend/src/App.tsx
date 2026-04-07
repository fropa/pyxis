import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./views/Dashboard";
import TopologyView from "./views/Topology";
import IncidentsView from "./views/Incidents";
import OnboardingView from "./views/Onboarding";
import InstallAgent from "./views/InstallAgent";
import AdminView from "./views/Admin";
import PlaygroundView from "./views/Playground";
import TracesView from "./views/Traces";
import AssistantView from "./views/Assistant";
import { useAppStore } from "./store";
import { apiClient } from "./api/client";

export default function App() {
  const apiKey = useAppStore((s) => s.apiKey);
  const setApiKey = useAppStore((s) => s.setApiKey);

  // Always sync the API key from the server on load.
  // This handles redeployments where the DB is wiped and a new tenant/key is created —
  // without this, the browser keeps the old key and all agent installs use it (401s).
  useEffect(() => {
    apiClient.get<{ api_key: string | null }>("/api/v1/tenants/setup/key")
      .then((r) => {
        const serverKey = r.data.api_key;
        if (serverKey && serverKey !== apiKey) {
          setApiKey(serverKey);
        }
      })
      .catch(() => {});
  }, []);

  if (!apiKey) {
    return <OnboardingView />;
  }

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Layout>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/topology" element={<TopologyView />} />
          <Route path="/incidents" element={<IncidentsView />} />
          <Route path="/onboarding" element={<OnboardingView />} />
          <Route path="/install" element={<InstallAgent />} />
          <Route path="/admin" element={<AdminView />} />
          <Route path="/playground" element={<PlaygroundView />} />
          <Route path="/traces" element={<TracesView />} />
          <Route path="/assistant" element={<AssistantView />} />
        </Routes>
      </Layout>
    </BrowserRouter>
  );
}
