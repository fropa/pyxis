import { useQuery } from "@tanstack/react-query";
import { X, GitCommit, ArrowRight, AlertCircle, Loader2 } from "lucide-react";
import { api } from "../../api/client";
import type { FlowChain } from "../../api/client";

const SOURCE_META: Record<string, { label: string; color: string; dot: string }> = {
  request_id:      { label: "Request ID (exact)",     color: "text-emerald-400", dot: "bg-emerald-400" },
  upstream_addr:   { label: "Upstream addr (direct)", color: "text-blue-400",    dot: "bg-blue-400"    },
  haproxy_routing: { label: "HAProxy routing",        color: "text-sky-400",     dot: "bg-sky-400"     },
  x_forwarded_for: { label: "X-Forwarded-For",        color: "text-amber-400",   dot: "bg-amber-400"   },
  cf_ray:          { label: "Cloudflare Ray",         color: "text-orange-400",  dot: "bg-orange-400"  },
};

function ConfidenceBadge({ c }: { c: number }) {
  const pct = Math.round(c * 100);
  const cls = pct >= 90 ? "bg-emerald-500/20 text-emerald-300" :
              pct >= 75 ? "bg-blue-500/20 text-blue-300" :
                          "bg-amber-500/20 text-amber-300";
  return <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>{pct}%</span>;
}

function FlowRow({ chain }: { chain: FlowChain }) {
  const srcMeta = SOURCE_META[chain.source] ?? SOURCE_META[chain.sources?.[0]] ?? {
    label: chain.source, color: "text-slate-400", dot: "bg-slate-400",
  };

  return (
    <div className="flex items-center gap-3 py-2.5 px-4 border-b border-[#1e2533] hover:bg-[#1a1f2e] transition-colors">
      {/* Hop chain */}
      <div className="flex items-center gap-1.5 flex-1 flex-wrap min-w-0">
        {chain.hops.map((hop, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <div className="flex flex-col items-center">
              <span className="text-[12px] font-medium text-slate-200 whitespace-nowrap max-w-[130px] truncate">
                {hop.node}
              </span>
              {hop.avg_ms > 0 && (
                <span className="text-[10px] text-slate-500">{hop.avg_ms}ms</span>
              )}
            </div>
            {i < chain.hops.length - 1 && (
              <ArrowRight size={12} className="text-slate-600 flex-shrink-0" />
            )}
          </div>
        ))}
      </div>

      {/* Right side: count + confidence + source */}
      <div className="flex items-center gap-2.5 flex-shrink-0">
        <span className="text-[11px] text-slate-400 whitespace-nowrap">
          {chain.count.toLocaleString()} req
        </span>
        <ConfidenceBadge c={chain.confidence} />
        <div className="flex items-center gap-1">
          <span className={`w-1.5 h-1.5 rounded-full ${srcMeta.dot}`} />
          <span className={`text-[10px] ${srcMeta.color} whitespace-nowrap`}>{srcMeta.label}</span>
        </div>
      </div>
    </div>
  );
}

export default function FlowPanel({ onClose }: { onClose: () => void }) {
  const { data: flows, isLoading, isError } = useQuery({
    queryKey: ["topology-flows"],
    queryFn: api.topology.flows,
    refetchInterval: 30_000,
  });

  return (
    <div className="fixed inset-x-0 bottom-0 z-50 bg-[#0d1117] border-t border-[#1e2533] shadow-2xl"
         style={{ maxHeight: "50vh" }}>
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-[#1e2533]">
        <div className="flex items-center gap-2.5">
          <GitCommit size={14} className="text-slate-400" />
          <div>
            <h2 className="text-[13px] font-semibold text-slate-100">Request Flows</h2>
            <p className="text-[11px] text-slate-500">
              Reconstructed from logs — how requests travel through your infrastructure
            </p>
          </div>
        </div>

        {/* Source legend */}
        <div className="flex items-center gap-4 mr-8">
          {Object.entries(SOURCE_META).map(([key, meta]) => (
            <div key={key} className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${meta.dot}`} />
              <span className={`text-[10px] ${meta.color}`}>{meta.label}</span>
            </div>
          ))}
        </div>

        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-[#1e2533] transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className="overflow-y-auto" style={{ maxHeight: "calc(50vh - 56px)" }}>
        {isLoading && (
          <div className="flex items-center justify-center gap-2 py-10 text-slate-500">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-[13px]">Reconstructing flows…</span>
          </div>
        )}

        {isError && (
          <div className="flex items-center justify-center gap-2 py-10 text-slate-500">
            <AlertCircle size={14} className="text-red-400" />
            <span className="text-[13px] text-red-400">Failed to load flows</span>
          </div>
        )}

        {!isLoading && !isError && (!flows || flows.length === 0) && (
          <div className="py-10 text-center">
            <p className="text-[13px] text-slate-400 mb-1">No flow data detected yet</p>
            <p className="text-[11px] text-slate-600 max-w-md mx-auto">
              Ensure nginx logs include <code className="text-slate-400">$upstream_addr</code>,{" "}
              <code className="text-slate-400">$request_id</code> and{" "}
              <code className="text-slate-400">$http_x_forwarded_for</code>.
              Open a node's Verbosity tab to see exact config fixes.
            </p>
          </div>
        )}

        {flows && flows.map((chain, i) => (
          <FlowRow key={i} chain={chain} />
        ))}
      </div>
    </div>
  );
}
