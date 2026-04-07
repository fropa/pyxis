import axios from "axios";
import { useAppStore } from "../store";

const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export const apiClient = axios.create({ baseURL: BASE_URL });

// Inject API key from store into every request
apiClient.interceptors.request.use((config) => {
  const apiKey = useAppStore.getState().apiKey;
  if (apiKey) config.headers["X-API-Key"] = apiKey;
  return config;
});

export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const apiMessage = error.response?.data?.detail;
    if (typeof apiMessage === "string" && apiMessage.trim()) {
      return apiMessage;
    }
    if (error.message) {
      return error.message;
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Check that the backend is running and VITE_API_URL points to the correct server.";
}

// ── Types ─────────────────────────────────────────────────────────────────────

export interface TopologyNode {
  id: string;
  external_id: string;
  name: string;
  kind: string;
  namespace: string | null;
  cluster: string | null;
  status: "healthy" | "degraded" | "down" | "unknown";
  labels: Record<string, string>;
  metadata: Record<string, unknown>;
}

export interface TopologyEdge {
  id: string;
  source_id: string;
  target_id: string;
  kind: string;
  confidence: number;
  last_seen: string | null;
  observation_count: number;
}

export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export interface Incident {
  id: string;
  title: string;
  severity: "low" | "medium" | "high" | "critical";
  status: "open" | "investigating" | "resolved" | "false_positive";
  started_at: string;
  resolved_at: string | null;
  rca_summary: string | null;
  rca_full: string | null;
  rca_confidence: number | null;
  cited_knowledge: string[];
  similar_incident_id: string | null;
  postmortem: string | null;
  parent_incident_id: string | null;
  storm_size: number;
}

export interface KnowledgeSource {
  id: string;
  repo_url: string;
  repo_type: string;
  index_status: "pending" | "indexing" | "ready" | "error";
  last_indexed_at: string | null;
  error_message: string | null;
}

// ── API calls ─────────────────────────────────────────────────────────────────

export const api = {
  topology: {
    get: () => apiClient.get<Topology>("/api/v1/topology/").then((r) => r.data),
    discover: () => apiClient.post<{ edges_found: number; nodes_found: number; sources: string[]; last_run: string }>("/api/v1/topology/discover").then((r) => r.data),
    stats: () => apiClient.get<{ node_count: number; edge_count: number; auto_discovered_nodes: number; edge_kinds: Record<string, number> }>("/api/v1/topology/stats").then((r) => r.data),
  },
  incidents: {
    list: (params?: { status_filter?: string }) =>
      apiClient.get<Incident[]>("/api/v1/incidents/", { params }).then((r) => r.data),
    get: (id: string) =>
      apiClient.get<Incident>(`/api/v1/incidents/${id}`).then((r) => r.data),
    update: (id: string, payload: Partial<Incident>) =>
      apiClient.patch<Incident>(`/api/v1/incidents/${id}`, payload).then((r) => r.data),
    postmortem: (id: string) =>
      apiClient.post<{ postmortem: string }>(`/api/v1/incidents/${id}/postmortem`).then((r) => r.data),
  },
  knowledge: {
    listSources: () =>
      apiClient.get<KnowledgeSource[]>("/api/v1/knowledge/sources").then((r) => r.data),
    addSource: (payload: { repo_url: string; repo_type: string; access_token?: string }) =>
      apiClient.post<KnowledgeSource>("/api/v1/knowledge/sources", payload).then((r) => r.data),
    reindex: (id: string) =>
      apiClient.post(`/api/v1/knowledge/sources/${id}/reindex`).then((r) => r.data),
  },
  notifications: {
    list: () =>
      apiClient.get<NotificationChannel[]>("/api/v1/notifications/").then((r) => r.data),
    create: (payload: { name: string; kind: string; config: Record<string, unknown>; min_severity?: string }) =>
      apiClient.post<NotificationChannel>("/api/v1/notifications/", payload).then((r) => r.data),
    delete: (id: string) =>
      apiClient.delete(`/api/v1/notifications/${id}`).then((r) => r.data),
  },
  heatmap: {
    get: (days = 90) =>
      apiClient.get<HeatmapEntry[]>("/api/v1/incidents/heatmap", { params: { days } }).then((r) => r.data),
  },
  runbooks: {
    list: () => apiClient.get<Runbook[]>("/api/v1/runbooks/").then((r) => r.data),
    forIncident: (incidentId: string) =>
      apiClient.get<Runbook | null>(`/api/v1/runbooks/incident/${incidentId}`).then((r) => r.data),
    delete: (id: string) => apiClient.delete(`/api/v1/runbooks/${id}`).then((r) => r.data),
  },
  deployEvents: {
    list: (limit = 50) =>
      apiClient.get<DeployEvent[]>("/api/v1/deploy-events/", { params: { limit } }).then((r) => r.data),
    create: (payload: { service: string; version?: string; deployed_by?: string; environment?: string }) =>
      apiClient.post<DeployEvent>("/api/v1/deploy-events/", payload).then((r) => r.data),
  },
  admin: {
    tenantStats: () =>
      apiClient.get<TenantStats[]>("/api/v1/tenants/stats").then((r) => r.data),
  },
  analyze: {
    logs: (payload: { logs: string; context?: string }) =>
      apiClient.post<LogAnalysisResponse>("/api/v1/analyze/logs", payload).then((r) => r.data),
  },
  traces: {
    ingest: (spans: object[]) =>
      apiClient.post("/api/v1/traces/", { spans }).then((r) => r.data),
    services: (hours = 1) =>
      apiClient.get<ServiceSummary[]>("/api/v1/traces/services", { params: { hours } }).then((r) => r.data),
    timeseries: (service: string, hours = 1) =>
      apiClient.get<TimeseriesPoint[]>(`/api/v1/traces/services/${encodeURIComponent(service)}/timeseries`, { params: { hours } }).then((r) => r.data),
    recent: (params?: { hours?: number; service?: string; limit?: number }) =>
      apiClient.get<TraceOut[]>("/api/v1/traces/recent", { params }).then((r) => r.data),
  },
  assistant: {
    chat: (payload: { question: string; history: { role: string; content: string }[] }) =>
      apiClient.post<{ answer: string }>("/api/v1/assistant/chat", payload).then((r) => r.data),
  },
};

export interface NotificationChannel {
  id: string;
  name: string;
  kind: string;
  min_severity: string;
  event_types: string[];
  is_active: boolean;
}

export interface HeatmapEntry {
  date: string;
  count: number;
}

export interface Runbook {
  id: string;
  incident_id: string;
  title: string;
  content: string;
  created_at: string;
}

export interface DeployEvent {
  id: string;
  service: string;
  version: string | null;
  deployed_by: string | null;
  environment: string;
  deployed_at: string;
  created_at: string;
}

export interface TenantStats {
  id: string;
  name: string;
  plan: string;
  total_incidents: number;
  open_incidents: number;
  resolved_last_7d: number;
  health_score: number;
}

export interface LogAnalysisResponse {
  analysis: string;
  confidence: number;
}

export interface ServiceSummary {
  service: string;
  request_count: number;
  error_count: number;
  error_rate: number;
  avg_ms: number;
  p99_ms: number;
  p50_ms: number;
}

export interface TimeseriesPoint {
  bucket: string;
  p99_ms: number;
  p50_ms: number;
  avg_ms: number;
  request_count: number;
  error_count: number;
}

export interface TraceOut {
  trace_id: string;
  service: string;
  operation: string;
  duration_ms: number;
  status: string;
  status_code: number | null;
  span_count: number;
  started_at: string;
}
