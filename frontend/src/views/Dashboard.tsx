import { useQuery, keepPreviousData } from "@tanstack/react-query";
import {
  AlertTriangle, Server, CheckCircle, TrendingUp, Activity,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { api, getErrorMessage } from "../api/client";
import AlertFeed from "../components/alerts/AlertFeed";
import IncidentPanel from "../components/incidents/IncidentPanel";
import IncidentHeatmap from "../components/heatmap/IncidentHeatmap";
import { Badge } from "../components/ui/Badge";
import { QueryErrorState } from "../components/ui/QueryErrorState";
import { StatusDot } from "../components/ui/StatusDot";
import { SkeletonCard, Skeleton } from "../components/ui/Skeleton";
import { useAppStore } from "../store";
import clsx from "clsx";

// ── Stat card ──────────────────────────────────────────────────────────────────

const STAT_STYLES = {
  danger:  { icon: "bg-danger-bg text-danger",    dot: "bg-danger" },
  accent:  { icon: "bg-accent-muted text-accent",  dot: "bg-accent" },
  success: { icon: "bg-success-bg text-success",   dot: "bg-success" },
  warning: { icon: "bg-warning-bg text-warning",   dot: "bg-warning" },
} as const;

function StatCard({
  label,
  value,
  icon: Icon,
  color,
  sub,
}: {
  label: string;
  value: number | string;
  icon: React.ElementType;
  color: keyof typeof STAT_STYLES;
  sub?: string;
}) {
  const s = STAT_STYLES[color];
  return (
    <div className="bg-surface rounded-xl border border-border shadow-card p-5 hover:shadow-md transition-shadow duration-200">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold text-text-3 uppercase tracking-wider mb-2">
            {label}
          </p>
          <p className="text-3xl font-bold text-text-1 tabular-nums leading-none mb-1.5">
            {value}
          </p>
          {sub && <p className="text-[12px] text-text-3">{sub}</p>}
        </div>
        <div className={clsx("w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0", s.icon)}>
          <Icon size={18} />
        </div>
      </div>
    </div>
  );
}

// ── Incident row ───────────────────────────────────────────────────────────────

function IncidentRow({
  inc,
}: {
  inc: ReturnType<typeof api.incidents.list> extends Promise<infer T>
    ? T[number]
    : never;
}) {
  const setActive = useAppStore((s) => s.setActiveIncidentId);
  return (
    <tr
      onClick={() => setActive(inc.id)}
      className="group border-b border-border/70 last:border-b-0 hover:bg-raised cursor-pointer transition-colors"
    >
      <td className="px-5 py-3">
        <div className="flex items-center gap-2.5">
          <StatusDot status={inc.status} size="sm" />
          <span className="text-[13px] text-text-1 font-medium group-hover:text-accent-text transition-colors max-w-xs truncate">
            {inc.title}
          </span>
        </div>
      </td>
      <td className="px-5 py-3">
        <Badge severity={inc.severity}>{inc.severity}</Badge>
      </td>
      <td className="px-5 py-3">
        <Badge status={inc.status}>{inc.status.replace("_", " ")}</Badge>
      </td>
      <td className="px-5 py-3 text-[12px] text-text-3 whitespace-nowrap">
        {formatDistanceToNow(new Date(inc.started_at), { addSuffix: true })}
      </td>
      <td className="px-5 py-3">
        {inc.rca_confidence != null ? (
          <div className="flex items-center gap-2">
            <div className="w-16 h-1.5 bg-raised border border-border rounded-full overflow-hidden">
              <div
                className={clsx(
                  "h-full rounded-full transition-all",
                  inc.rca_confidence > 0.7 ? "bg-success" :
                  inc.rca_confidence > 0.4 ? "bg-warning" : "bg-danger"
                )}
                style={{ width: `${Math.round(inc.rca_confidence * 100)}%` }}
              />
            </div>
            <span className="text-[12px] text-text-3 tabular-nums">
              {Math.round(inc.rca_confidence * 100)}%
            </span>
          </div>
        ) : (
          <span className="text-[12px] text-text-4">—</span>
        )}
      </td>
    </tr>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const {
    data: topology,
    isLoading: topoLoading,
    isError: topoError,
    error: topologyError,
  } = useQuery({
    queryKey: ["topology"],
    queryFn: api.topology.get,
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });
  const {
    data: incidents,
    isLoading: incLoading,
    isError: incError,
    error: incidentsError,
  } = useQuery({
    queryKey: ["incidents"],
    queryFn: () => api.incidents.list(),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });

  const openIncidents = incidents?.filter((i) => i.status === "open").length ?? 0;
  const totalNodes    = topology?.nodes.length ?? 0;
  const healthyNodes  = topology?.nodes.filter((n) => n.status === "healthy").length ?? 0;
  const resolvedToday = incidents?.filter(
    (i) => i.resolved_at && new Date(i.resolved_at) > new Date(Date.now() - 86_400_000)
  ).length ?? 0;

  return (
    <div className="flex h-full min-h-0">
      {/* Main */}
      <div className="flex-1 overflow-y-auto min-w-0">
        <div className="p-6 space-y-6 max-w-[1200px]">

          {/* Page header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-xl font-semibold text-text-1">Overview</h1>
              <p className="text-[13px] text-text-3 mt-0.5">
                {new Date().toLocaleDateString("en-US", {
                  weekday: "long",
                  month: "long",
                  day: "numeric",
                })}
              </p>
            </div>
            <div className="flex items-center gap-2 px-3 py-1.5 bg-surface border border-border rounded-lg shadow-sm">
              <span className="w-2 h-2 rounded-full bg-success animate-pulse" />
              <span className="text-[12px] text-text-2 font-medium">Live monitoring</span>
            </div>
          </div>

          {/* Stat cards — skeleton only on first load, not on every refetch */}
          {topoLoading && incLoading ? (
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : (
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
              <StatCard
                label="Open Incidents"
                value={openIncidents}
                icon={AlertTriangle}
                color="danger"
                sub={openIncidents > 0 ? "Needs attention" : "All clear"}
              />
              <StatCard
                label="Total Nodes"
                value={totalNodes}
                icon={Server}
                color="accent"
                sub={`${healthyNodes} healthy`}
              />
              <StatCard
                label="Healthy Nodes"
                value={healthyNodes}
                icon={CheckCircle}
                color="success"
                sub={
                  totalNodes > 0
                    ? `${Math.round((healthyNodes / Math.max(totalNodes, 1)) * 100)}% uptime`
                    : "No nodes yet"
                }
              />
              <StatCard
                label="Resolved Today"
                value={resolvedToday}
                icon={TrendingUp}
                color="warning"
                sub="Last 24 hours"
              />
            </div>
          )}

          {/* Incidents table */}
          <div className="bg-surface border border-border rounded-xl shadow-card overflow-hidden">
            <div className="flex items-center justify-between px-5 py-4 border-b border-border">
              <h2 className="text-[13px] font-semibold text-text-1">Recent Incidents</h2>
              <a
                href="/incidents"
                className="text-[12px] text-accent-text font-medium hover:text-accent transition-colors"
              >
                View all →
              </a>
            </div>

            {incError ? (
              <div className="p-5">
                <QueryErrorState message={getErrorMessage(incidentsError)} />
              </div>
            ) : incLoading ? (
              <div className="p-5 space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="w-2 h-2 rounded-full" />
                    <Skeleton className="h-3 flex-1" />
                    <Skeleton className="h-5 w-16 rounded-md" />
                    <Skeleton className="h-5 w-20 rounded-md" />
                    <Skeleton className="h-3 w-20" />
                  </div>
                ))}
              </div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-border/60">
                    {["Title", "Severity", "Status", "When", "AI Confidence"].map((h) => (
                      <th
                        key={h}
                        className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-text-3"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(incidents ?? []).slice(0, 10).map((inc) => (
                    <IncidentRow key={inc.id} inc={inc} />
                  ))}
                </tbody>
              </table>
            )}

            {incidents !== undefined && incidents.length === 0 && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <div className="w-12 h-12 rounded-full bg-success-bg border border-success-border flex items-center justify-center mb-3">
                  <CheckCircle size={22} className="text-success" />
                </div>
                <p className="text-[14px] font-semibold text-text-1">All systems normal</p>
                <p className="text-[12px] text-text-3 mt-1">No incidents detected</p>
              </div>
            )}
          </div>

          <IncidentHeatmap />

          {topoError && (
            <QueryErrorState
              title="Unable to load topology"
              message={getErrorMessage(topologyError)}
            />
          )}
        </div>
      </div>

      {/* Alert feed */}
      <div className="w-[300px] flex-shrink-0 border-l border-border bg-surface overflow-hidden flex flex-col">
        <AlertFeed />
      </div>

      <IncidentPanel />
    </div>
  );
}
