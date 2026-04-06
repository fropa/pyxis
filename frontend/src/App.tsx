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

export default function App() {
  const apiKey = useAppStore((s) => s.apiKey);

  if (!apiKey) {
    return <OnboardingView />;
  }

  return (
    <BrowserRouter>
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
