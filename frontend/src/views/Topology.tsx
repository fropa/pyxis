import { useQuery, keepPreviousData } from "@tanstack/react-query"; // keepPreviousData keeps graph stable during refetch
import { Network } from "lucide-react";
import TopologyGraph from "../components/topology/TopologyGraph";
import AlertFeed from "../components/alerts/AlertFeed";
import IncidentPanel from "../components/incidents/IncidentPanel";
import { api, getErrorMessage } from "../api/client";
import { QueryErrorState } from "../components/ui/QueryErrorState";

export default function TopologyView() {
  const { data: topology, isLoading, isError, error } = useQuery({
    queryKey: ["topology"],
    queryFn: api.topology.get,
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

  const nodeCount    = topology?.nodes.length ?? 0;
  const healthyCount = topology?.nodes.filter((n) => n.status === "healthy").length ?? 0;
  const degradedCount = topology?.nodes.filter((n) => n.status === "degraded").length ?? 0;
  const downCount    = topology?.nodes.filter((n) => n.status === "down").length ?? 0;

  return (
    <div className="flex h-full min-h-0">
      {/* Graph */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar */}
        <div className="flex-shrink-0 flex items-center justify-between px-5 py-3 bg-surface border-b border-border">
          <div className="flex items-center gap-2.5">
            <Network size={14} className="text-text-3" />
            <h1 className="text-[13px] font-semibold text-text-1">
              Infrastructure Topology
            </h1>
          </div>

          {topology && (
            <div className="flex items-center gap-4 text-[12px]">
              <span className="text-text-3">
                <span className="font-semibold text-text-1">{nodeCount}</span> nodes
              </span>
              {healthyCount > 0 && (
                <span className="flex items-center gap-1.5 text-success-text font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-success" />
                  {healthyCount} healthy
                </span>
              )}
              {degradedCount > 0 && (
                <span className="flex items-center gap-1.5 text-warning-text font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-warning" />
                  {degradedCount} degraded
                </span>
              )}
              {downCount > 0 && (
                <span className="flex items-center gap-1.5 text-danger-text font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-danger" />
                  {downCount} down
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex-1 relative min-h-0 bg-raised">
          {isError && (
            <div className="absolute inset-0 p-6">
              <QueryErrorState
                title="Unable to load topology"
                message={getErrorMessage(error)}
              />
            </div>
          )}

          {!isError && isLoading && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3">
              <div className="w-8 h-8 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
              <p className="text-[13px] text-text-3">Loading topology…</p>
            </div>
          )}

          {!isError && topology !== undefined && topology.nodes.length === 0 && (
            <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-8">
              <div className="w-14 h-14 rounded-full bg-surface border border-border flex items-center justify-center mb-3 shadow-card">
                <Network size={22} className="text-text-4" />
              </div>
              <p className="text-[14px] font-semibold text-text-1">No nodes discovered yet</p>
              <p className="text-[13px] text-text-3 mt-1.5 max-w-xs leading-relaxed">
                Install the agent on your servers or Kubernetes cluster and nodes will appear here automatically.
              </p>
            </div>
          )}

          {!isError && topology && topology.nodes.length > 0 && (
            <TopologyGraph topology={topology} />
          )}
        </div>
      </div>

      {/* Feed */}
      <div className="w-[300px] flex-shrink-0 border-l border-border bg-surface overflow-hidden flex flex-col">
        <AlertFeed />
      </div>

      <IncidentPanel />
    </div>
  );
}
