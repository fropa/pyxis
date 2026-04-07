import { useState } from "react";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import { Network, RefreshCw, CheckCircle2, GitBranch, Zap } from "lucide-react";
import TopologyGraph from "../components/topology/TopologyGraph";
import NodeLogsPanel from "../components/topology/NodeLogsPanel";
import BroadcastConsole from "../components/topology/BroadcastConsole";
import AlertFeed from "../components/alerts/AlertFeed";
import IncidentPanel from "../components/incidents/IncidentPanel";
import { api, getErrorMessage } from "../api/client";
import type { TopologyNode } from "../api/client";
import { QueryErrorState } from "../components/ui/QueryErrorState";

const KIND_LABEL: Record<string, string> = {
  calls:          "Calls",
  dependency:     "Dependency",
  "co-deployed":  "Co-deployed",
  "co-occurrence":"Incident",
  network:        "Network",
};

const KIND_COLOR: Record<string, string> = {
  calls:          "bg-indigo-500",
  dependency:     "bg-amber-500",
  "co-deployed":  "bg-slate-400",
  "co-occurrence":"bg-purple-500",
  network:        "bg-slate-300",
};

export default function TopologyView() {
  const qc = useQueryClient();
  const [discoverResult, setDiscoverResult] = useState<{ edges_found: number; sources: string[] } | null>(null);
  const [selectedNode, setSelectedNode] = useState<TopologyNode | null>(null);
  const [showBroadcast, setShowBroadcast] = useState(false);

  const { data: topology, isLoading, isError, error } = useQuery({
    queryKey: ["topology"],
    queryFn: api.topology.get,
    refetchInterval: 15_000,
    placeholderData: keepPreviousData,
  });

  const { data: stats } = useQuery({
    queryKey: ["topology-stats"],
    queryFn: api.topology.stats,
    refetchInterval: 60_000,
  });

  const discoverMutation = useMutation({
    mutationFn: api.topology.discover,
    onSuccess: (data) => {
      setDiscoverResult({ edges_found: data.edges_found, sources: data.sources });
      qc.invalidateQueries({ queryKey: ["topology"] });
      qc.invalidateQueries({ queryKey: ["topology-stats"] });
      setTimeout(() => setDiscoverResult(null), 8000);
    },
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

          <div className="flex items-center gap-4">
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

            {/* Broadcast button */}
            {topology && topology.nodes.length > 0 && (
              <button
                onClick={() => setShowBroadcast(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium bg-[#1e1e35] text-[#a5b4fc] border border-[#7c8cf8]/20 rounded-lg hover:bg-[#252550] hover:border-[#7c8cf8]/40 transition-all"
              >
                <Zap size={11} />
                Broadcast
              </button>
            )}

            {/* Re-discover button */}
            <button
              onClick={() => discoverMutation.mutate()}
              disabled={discoverMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-60 transition-all shadow-sm"
            >
              <RefreshCw size={11} className={discoverMutation.isPending ? "animate-spin" : ""} />
              {discoverMutation.isPending ? "Discovering…" : "Re-discover"}
            </button>
          </div>
        </div>

        {/* Discovery result banner */}
        {discoverResult && (
          <div className="flex-shrink-0 flex items-center gap-2 px-5 py-2.5 bg-success/10 border-b border-success/20 text-[12px] text-success-text">
            <CheckCircle2 size={13} className="flex-shrink-0" />
            Discovery complete — <strong>{discoverResult.edges_found}</strong> edges mapped
            from {discoverResult.sources.join(", ") || "0 sources"}
          </div>
        )}

        {discoverMutation.isError && (
          <div className="flex-shrink-0 px-5 py-2 bg-danger/10 border-b border-danger/20 text-[12px] text-danger">
            {getErrorMessage(discoverMutation.error)}
          </div>
        )}

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
                Install the agent or send spans/logs and click Re-discover to auto-map your services.
              </p>
              <button
                onClick={() => discoverMutation.mutate()}
                disabled={discoverMutation.isPending}
                className="mt-4 flex items-center gap-2 px-4 py-2 bg-accent text-white text-[13px] font-medium rounded-xl hover:bg-accent/90 disabled:opacity-60 transition-all"
              >
                <RefreshCw size={12} className={discoverMutation.isPending ? "animate-spin" : ""} />
                Run Discovery Now
              </button>
            </div>
          )}

          {!isError && topology && topology.nodes.length > 0 && (
            <TopologyGraph topology={topology} onNodeSelect={setSelectedNode} />
          )}
        </div>

        {/* Edge legend + stats */}
        {stats && (stats.edge_count > 0 || nodeCount > 0) && (
          <div className="flex-shrink-0 flex items-center gap-5 px-5 py-2 border-t border-border bg-surface">
            <div className="flex items-center gap-1.5">
              <GitBranch size={11} className="text-text-4" />
              <span className="text-[11px] text-text-3 font-medium">Edge sources:</span>
            </div>
            {Object.entries(stats.edge_kinds).map(([kind, count]) => (
              <div key={kind} className="flex items-center gap-1.5">
                <span className={`w-2 h-2 rounded-full ${KIND_COLOR[kind] ?? "bg-slate-400"}`} />
                <span className="text-[11px] text-text-3">
                  {KIND_LABEL[kind] ?? kind} ({count})
                </span>
              </div>
            ))}
            <span className="ml-auto text-[11px] text-text-4">
              {stats.auto_discovered_nodes} auto-discovered services
            </span>
          </div>
        )}
      </div>

      {/* Feed */}
      <div className="w-[300px] flex-shrink-0 border-l border-border bg-surface overflow-hidden flex flex-col">
        <AlertFeed />
      </div>

      <IncidentPanel />
      {selectedNode && (
        <NodeLogsPanel node={selectedNode} onClose={() => setSelectedNode(null)} />
      )}
      {showBroadcast && topology && (
        <BroadcastConsole
          nodes={topology.nodes}
          onClose={() => setShowBroadcast(false)}
        />
      )}
    </div>
  );
}
