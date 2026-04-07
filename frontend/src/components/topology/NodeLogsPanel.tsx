import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, Loader2, Terminal, ChevronDown, ChevronRight, Trash2, ScrollText, TerminalSquare } from "lucide-react";
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

interface HistoryEntry {
  cmd: string;
  output: string;
  exit_code: number;
  duration_ms: number;
}

interface Props {
  node: TopologyNode;
  onClose: () => void;
}

type Tab = "logs" | "console";

export default function NodeLogsPanel({ node, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("logs");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyIdx, setHistoryIdx] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const consoleBottomRef = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: () => api.topology.deleteNode(node.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["topology"] });
      qc.invalidateQueries({ queryKey: ["topology-stats"] });
      onClose();
    },
  });

  const execMutation = useMutation({
    mutationFn: (command: string) => api.exec.run(node.id, command),
    onSuccess: (data, command) => {
      setHistory((h) => [...h, { cmd: command, output: data.output, exit_code: data.exit_code, duration_ms: data.duration_ms }]);
      setCmd("");
      setHistoryIdx(-1);
    },
    onError: (err, command) => {
      setHistory((h) => [...h, { cmd: command, output: `Error: ${getErrorMessage(err)}`, exit_code: -1, duration_ms: 0 }]);
      setCmd("");
    },
  });

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["node-logs", node.id],
    queryFn: () => api.topology.nodeLogs(node.id),
    refetchInterval: 10_000,
  });

  const sources = data ? Object.keys(data.by_source).sort() : [];

  // Scroll console to bottom on new output
  useEffect(() => {
    consoleBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, execMutation.isPending]);

  // Focus input when switching to console tab
  useEffect(() => {
    if (tab === "console") setTimeout(() => inputRef.current?.focus(), 50);
  }, [tab]);

  const cmdHistory = history.map((h) => h.cmd);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && cmd.trim() && !execMutation.isPending) {
      execMutation.mutate(cmd.trim());
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      const idx = historyIdx + 1;
      if (idx < cmdHistory.length) {
        setHistoryIdx(idx);
        setCmd(cmdHistory[cmdHistory.length - 1 - idx]);
      }
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const idx = historyIdx - 1;
      if (idx < 0) { setHistoryIdx(-1); setCmd(""); }
      else { setHistoryIdx(idx); setCmd(cmdHistory[cmdHistory.length - 1 - idx]); }
    }
  }

  return (
    <div className="fixed inset-y-0 right-0 w-[560px] bg-[#0f0f1a] border-l border-[#2d2d4e] flex flex-col z-50 shadow-2xl animate-slide-in">

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
        <div className="flex items-center gap-1">
          {confirmDelete ? (
            <>
              <span className="text-[11px] text-red-400 mr-1">Remove node?</span>
              <button
                onClick={() => deleteMutation.mutate()}
                disabled={deleteMutation.isPending}
                className="px-2 py-1 text-[11px] font-semibold rounded bg-red-600 text-white hover:bg-red-500 disabled:opacity-50 transition-colors"
              >
                {deleteMutation.isPending ? "…" : "Yes"}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="px-2 py-1 text-[11px] rounded text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
              >
                No
              </button>
            </>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="p-1.5 rounded-lg text-slate-600 hover:text-red-400 hover:bg-white/5 transition-colors"
              title="Remove from topology"
            >
              <Trash2 size={14} />
            </button>
          )}
          <button onClick={onClose} className="p-1.5 rounded-lg text-slate-500 hover:text-white hover:bg-white/5 transition-colors">
            <X size={15} />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#2d2d4e] flex-shrink-0">
        {(["logs", "console"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              "flex items-center gap-1.5 px-4 py-2 text-[12px] font-medium border-b-2 transition-colors",
              tab === t
                ? "border-[#7c8cf8] text-[#7c8cf8]"
                : "border-transparent text-slate-500 hover:text-slate-300"
            )}
          >
            {t === "logs" ? <ScrollText size={12} /> : <TerminalSquare size={12} />}
            {t === "logs" ? "Logs" : "Console"}
          </button>
        ))}
      </div>

      {/* ── Logs tab ── */}
      {tab === "logs" && (
        <div className="flex-1 overflow-y-auto">
          {isLoading && (
            <div className="flex items-center gap-2 p-5 text-slate-500 text-[13px]">
              <Loader2 size={14} className="animate-spin" /> Loading logs…
            </div>
          )}
          {isError && <p className="p-5 text-[12px] text-red-400">{getErrorMessage(error)}</p>}
          {data && sources.length === 0 && (
            <div className="p-5 text-[13px] text-slate-500">No logs received from this node yet.</div>
          )}
          {data && sources.map((source) => {
            const entries = data.by_source[source];
            const abbrev = SOURCE_ICON[source] ?? source.slice(0, 3).toUpperCase();
            const isOpen = !collapsed[source];
            return (
              <div key={source} className="border-b border-[#1e1e35]">
                <button
                  onClick={() => setCollapsed((c) => ({ ...c, [source]: !c[source] }))}
                  className="w-full flex items-center gap-2 px-4 py-2 hover:bg-white/5 transition-colors"
                >
                  {isOpen ? <ChevronDown size={12} className="text-slate-500" /> : <ChevronRight size={12} className="text-slate-500" />}
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#2d2d4e] text-[#7c8cf8] font-mono">{abbrev}</span>
                  <span className="text-[12px] font-semibold text-slate-300">{source}</span>
                  <span className="ml-auto text-[11px] text-slate-600">{entries.length} lines</span>
                </button>
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
      )}

      {/* ── Console tab ── */}
      {tab === "console" && (
        <>
          <div className="flex-1 overflow-y-auto p-4 space-y-3 font-mono text-[12px]">
            {history.length === 0 && !execMutation.isPending && (
              <p className="text-slate-600">Type a command below and press Enter.</p>
            )}
            {history.map((h, i) => (
              <div key={i}>
                <div className="flex items-center gap-2">
                  <span className="text-[#7c8cf8]">$</span>
                  <span className="text-slate-200">{h.cmd}</span>
                  <span className="ml-auto text-[10px] text-slate-600">{h.duration_ms}ms</span>
                  {h.exit_code !== 0 && (
                    <span className="text-[10px] text-red-400">exit {h.exit_code}</span>
                  )}
                </div>
                {h.output && (
                  <pre className="mt-1 text-slate-400 whitespace-pre-wrap break-all leading-relaxed pl-4 border-l border-[#2d2d4e]">
                    {h.output}
                  </pre>
                )}
              </div>
            ))}
            {execMutation.isPending && (
              <div className="flex items-center gap-2 text-slate-500">
                <Loader2 size={12} className="animate-spin" />
                <span>Running…</span>
              </div>
            )}
            <div ref={consoleBottomRef} />
          </div>

          {/* Input */}
          <div className="flex-shrink-0 border-t border-[#2d2d4e] flex items-center gap-2 px-4 py-3">
            <span className="text-[#7c8cf8] font-mono text-[13px]">$</span>
            <input
              ref={inputRef}
              value={cmd}
              onChange={(e) => setCmd(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={execMutation.isPending}
              placeholder="enter command…"
              className="flex-1 bg-transparent text-slate-200 font-mono text-[12px] outline-none placeholder:text-slate-700 disabled:opacity-50"
              autoComplete="off"
              spellCheck={false}
            />
            {execMutation.isPending && <Loader2 size={13} className="text-slate-500 animate-spin" />}
          </div>
        </>
      )}

      {tab === "logs" && data && (
        <div className="flex-shrink-0 px-4 py-2 border-t border-[#2d2d4e] text-[10px] text-slate-600">
          {sources.reduce((s, k) => s + data.by_source[k].length, 0)} log lines · refreshes every 10s
        </div>
      )}
    </div>
  );
}
