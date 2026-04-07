import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X, Loader2, Terminal, ChevronDown, ChevronRight } from "lucide-react";
import { api, getErrorMessage } from "../../api/client";
import type { TopologyNode } from "../../api/client";
import clsx from "clsx";

const LEVEL_COLOR: Record<string, string> = {
  critical: "text-red-400",
  error:    "text-red-400",
  warning:  "text-yellow-400",
  warn:     "text-yellow-400",
  info:     "text-slate-300",
  debug:    "text-slate-500",
};

const SOURCE_ICON: Record<string, string> = {
  syslog:      "SYS",
  k8s_event:   "K8S",
  ci_pipeline: "CI",
  app_log:     "APP",
  audit_log:   "AUD",
};

function fmt(ts: string) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

interface Props {
  node: TopologyNode;
  onClose: () => void;
}

export default function NodeLogsPanel({ node, onClose }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["node-logs", node.id],
    queryFn: () => api.topology.nodeLogs(node.id),
    refetchInterval: 10_000,
  });

  const sources = data ? Object.keys(data.by_source).sort() : [];

  return (
    <div className="fixed inset-y-0 right-0 w-[520px] bg-[#0f0f1a] border-l border-[#2d2d4e] flex flex-col z-50 shadow-2xl animate-slide-in">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[#2d2d4e] flex-shrink-0">
        <Terminal size={15} className="text-[#7c8cf8]" />
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-semibold text-white truncate">{node.name}</p>
          <p className="text-[11px] text-slate-500">
            {node.kind}
            {(node.metadata?.ip_address as string) && (
              <span className="ml-2 font-mono">{node.metadata.ip_address as string}</span>
            )}
          </p>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors"
        >
          <X size={15} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex items-center gap-2 p-5 text-slate-500 text-[13px]">
            <Loader2 size={14} className="animate-spin" />
            Loading logs…
          </div>
        )}

        {isError && (
          <p className="p-5 text-[12px] text-red-400">{getErrorMessage(error)}</p>
        )}

        {data && sources.length === 0 && (
          <div className="p-5 text-[13px] text-slate-500">
            No logs received from this node yet.
          </div>
        )}

        {data && sources.map((source) => {
          const entries = data.by_source[source];
          const abbrev = SOURCE_ICON[source] ?? source.slice(0, 3).toUpperCase();
          const isOpen = !collapsed[source];

          return (
            <div key={source} className="border-b border-[#1e1e35]">
              {/* Source header */}
              <button
                onClick={() => setCollapsed((c) => ({ ...c, [source]: !c[source] }))}
                className="w-full flex items-center gap-2 px-4 py-2 hover:bg-white/5 transition-colors"
              >
                {isOpen ? <ChevronDown size={12} className="text-slate-500" /> : <ChevronRight size={12} className="text-slate-500" />}
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#2d2d4e] text-[#7c8cf8] font-mono">
                  {abbrev}
                </span>
                <span className="text-[12px] font-semibold text-slate-300">{source}</span>
                <span className="ml-auto text-[11px] text-slate-600">{entries.length} lines</span>
              </button>

              {/* Log lines */}
              {isOpen && (
                <div className="px-4 pb-3 space-y-0.5">
                  {entries.map((e) => (
                    <div key={e.id} className="flex gap-2 text-[11px] font-mono leading-relaxed">
                      <span className="text-slate-600 flex-shrink-0 w-[64px]">{fmt(e.ts)}</span>
                      <span className={clsx("flex-shrink-0 w-[52px]", LEVEL_COLOR[e.level] ?? "text-slate-400")}>
                        {e.level.toUpperCase().slice(0, 4)}
                      </span>
                      <span className="text-slate-300 break-all">{e.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {data && (
        <div className="flex-shrink-0 px-4 py-2 border-t border-[#2d2d4e] text-[10px] text-slate-600">
          {sources.reduce((s, k) => s + data.by_source[k].length, 0)} log lines · refreshes every 10s
        </div>
      )}
    </div>
  );
}
