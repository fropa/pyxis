/**
 * BroadcastConsole — run one command on many nodes simultaneously.
 * Results stream in as each agent responds.
 */
import { useState, useCallback, useRef } from "react";
import {
  X, Zap, Play, Loader2, CheckCircle2, XCircle, Clock,
  Copy, Check, ChevronDown, ChevronRight, Wifi, WifiOff, AlertTriangle,
} from "lucide-react";
import clsx from "clsx";
import { api, getErrorMessage } from "../../api/client";
import type { TopologyNode } from "../../api/client";

// ── Types ─────────────────────────────────────────────────────────────────────

type RunStatus = "idle" | "running" | "success" | "error" | "timeout";

interface NodeResult {
  node_id: string;
  node_name: string;
  status: RunStatus;
  output: string;
  exit_code: number;
  duration_ms: number;
}

// ── Quick commands ─────────────────────────────────────────────────────────────

const QUICK_CMDS = [
  { label: "uptime",  cmd: "uptime" },
  { label: "df -h",   cmd: "df -h" },
  { label: "free -h", cmd: "free -h" },
  { label: "top snap", cmd: "top -bn1 | head -15" },
  { label: "who",     cmd: "who" },
  { label: "netstat", cmd: "ss -tulnp" },
  { label: "ps",      cmd: "ps aux --sort=-%cpu | head -10" },
  { label: "dmesg",   cmd: "dmesg | tail -10" },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtMs(ms: number) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

const STATUS_META: Record<RunStatus, { icon: React.ReactNode; color: string; label: string }> = {
  idle:    { icon: null, color: "text-slate-600", label: "queued" },
  running: { icon: <Loader2 size={13} className="animate-spin" />, color: "text-[#7c8cf8]", label: "running…" },
  success: { icon: <CheckCircle2 size={13} />, color: "text-emerald-400", label: "ok" },
  error:   { icon: <XCircle size={13} />, color: "text-red-400", label: "error" },
  timeout: { icon: <AlertTriangle size={13} />, color: "text-yellow-400", label: "timeout" },
};

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        (navigator.clipboard?.writeText(text) ?? Promise.resolve()).catch(() => {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta); ta.select();
          document.execCommand("copy"); document.body.removeChild(ta);
        });
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="p-1 rounded text-slate-600 hover:text-slate-300 transition-colors"
      title="Copy output"
    >
      {copied ? <Check size={11} className="text-emerald-400" /> : <Copy size={11} />}
    </button>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  nodes: TopologyNode[];
  onClose: () => void;
}

export default function BroadcastConsole({ nodes, onClose }: Props) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(nodes.filter((n) => n.status !== "down").map((n) => n.id))
  );
  const [cmd, setCmd] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<Map<string, NodeResult>>(new Map());
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedCount = selectedIds.size;
  const hasRun = results.size > 0;

  const statusCounts = (() => {
    let ok = 0, err = 0, running_ = 0, pending = 0;
    for (const r of results.values()) {
      if (r.status === "success") ok++;
      else if (r.status === "error" || r.status === "timeout") err++;
      else if (r.status === "running") running_++;
      else pending++;
    }
    return { ok, err, running: running_, pending };
  })();

  const toggleNode = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelectedIds(new Set(nodes.map((n) => n.id)));
  const selectNone = () => setSelectedIds(new Set());
  const selectHealthy = () =>
    setSelectedIds(new Set(nodes.filter((n) => n.status !== "down").map((n) => n.id)));

  const runBroadcast = useCallback(async () => {
    const command = cmd.trim();
    if (!command || !selectedCount || running) return;

    setRunning(true);
    setExpandedIds(new Set()); // collapse all to start fresh

    // Init all as running
    const init = new Map<string, NodeResult>();
    for (const id of selectedIds) {
      const node = nodes.find((n) => n.id === id)!;
      init.set(id, {
        node_id: id,
        node_name: node.name,
        status: "running",
        output: "",
        exit_code: -1,
        duration_ms: 0,
      });
    }
    setResults(new Map(init));

    // Fire all in parallel — update state as each completes
    const promises = Array.from(selectedIds).map((nodeId) => {
      const node = nodes.find((n) => n.id === nodeId)!;
      return api.exec.run(nodeId, command)
        .then((data) => {
          setResults((prev) => {
            const next = new Map(prev);
            next.set(nodeId, {
              node_id: nodeId,
              node_name: node.name,
              status: data.exit_code === 0 ? "success" : "error",
              output: data.output,
              exit_code: data.exit_code,
              duration_ms: data.duration_ms,
            });
            return next;
          });
          // Auto-expand errors so engineer sees the issue immediately
          if (data.exit_code !== 0) {
            setExpandedIds((prev) => new Set([...prev, nodeId]));
          }
        })
        .catch((err) => {
          const msg = getErrorMessage(err);
          const isTimeout = msg.toLowerCase().includes("timeout") || msg.includes("408");
          setResults((prev) => {
            const next = new Map(prev);
            next.set(nodeId, {
              node_id: nodeId,
              node_name: node.name,
              status: isTimeout ? "timeout" : "error",
              output: msg,
              exit_code: -1,
              duration_ms: 0,
            });
            return next;
          });
          setExpandedIds((prev) => new Set([...prev, nodeId]));
        });
    });

    await Promise.allSettled(promises);
    setRunning(false);
  }, [cmd, selectedIds, selectedCount, running, nodes]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") runBroadcast();
    if (e.key === "Escape") onClose();
  }

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Sort results: running first, then errors, then success
  const sortedResults = Array.from(results.values()).sort((a, b) => {
    const order: RunStatus[] = ["running", "timeout", "error", "idle", "success"];
    return order.indexOf(a.status) - order.indexOf(b.status);
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <div className="relative w-full max-w-4xl max-h-[90vh] bg-[#0d0d18] border border-[#252540] rounded-2xl shadow-2xl flex flex-col overflow-hidden">

        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-[#252540] bg-[#111124] flex-shrink-0">
          <div className="w-7 h-7 rounded-lg bg-[#7c8cf8]/15 flex items-center justify-center">
            <Zap size={14} className="text-[#a5b4fc]" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 className="text-[14px] font-semibold text-slate-200">Broadcast Console</h2>
            <p className="text-[11px] text-slate-600 mt-0.5">Run one command on multiple nodes simultaneously</p>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-600 hover:text-slate-300 hover:bg-white/5 transition-colors"
          >
            <X size={15} />
          </button>
        </div>

        <div className="flex flex-1 min-h-0">

          {/* Left: Node picker */}
          <div className="w-[220px] flex-shrink-0 border-r border-[#252540] flex flex-col bg-[#0a0a14]">
            <div className="px-4 py-3 border-b border-[#1e1e35]">
              <p className="text-[11px] font-semibold text-slate-400 mb-2">
                {selectedCount} of {nodes.length} selected
              </p>
              <div className="flex gap-1.5 flex-wrap">
                <button onClick={selectAll}
                  className="text-[10px] px-2 py-0.5 rounded bg-[#1e1e35] text-slate-500 hover:text-slate-300 transition-colors">
                  All
                </button>
                <button onClick={selectNone}
                  className="text-[10px] px-2 py-0.5 rounded bg-[#1e1e35] text-slate-500 hover:text-slate-300 transition-colors">
                  None
                </button>
                <button onClick={selectHealthy}
                  className="text-[10px] px-2 py-0.5 rounded bg-[#1e1e35] text-emerald-600 hover:text-emerald-400 transition-colors">
                  Healthy
                </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto py-2">
              {nodes.map((node) => {
                const checked = selectedIds.has(node.id);
                const res = results.get(node.id);
                const statusMeta = res ? STATUS_META[res.status] : null;
                return (
                  <button
                    key={node.id}
                    onClick={() => toggleNode(node.id)}
                    className={clsx(
                      "w-full flex items-center gap-2.5 px-4 py-2 text-left transition-colors",
                      checked ? "bg-[#7c8cf8]/8 hover:bg-[#7c8cf8]/12" : "hover:bg-white/[0.02]"
                    )}
                  >
                    {/* Checkbox */}
                    <span className={clsx(
                      "w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center transition-colors",
                      checked ? "bg-[#7c8cf8] border-[#7c8cf8]" : "border-[#3d3d5e] bg-transparent"
                    )}>
                      {checked && <Check size={9} className="text-white" strokeWidth={3} />}
                    </span>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[12px] text-slate-300 truncate font-mono">{node.name}</span>
                      </div>
                      <div className="flex items-center gap-1 mt-0.5">
                        {node.status === "healthy"
                          ? <Wifi size={9} className="text-emerald-500" />
                          : node.status === "down"
                          ? <WifiOff size={9} className="text-red-400" />
                          : <AlertTriangle size={9} className="text-yellow-400" />}
                        <span className="text-[10px] text-slate-600">{node.kind}</span>
                      </div>
                    </div>

                    {/* Live result status */}
                    {statusMeta && (
                      <span className={clsx("flex-shrink-0", statusMeta.color)}>
                        {statusMeta.icon}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Right: Command + results */}
          <div className="flex-1 flex flex-col min-w-0 min-h-0">

            {/* Command input */}
            <div className="flex-shrink-0 px-5 py-4 border-b border-[#1e1e35] bg-[#0f0f1e]">
              {/* Quick commands */}
              <div className="flex items-center gap-1.5 mb-3 flex-wrap">
                <span className="text-[10px] text-slate-700 font-medium mr-0.5">Quick:</span>
                {QUICK_CMDS.map((q) => (
                  <button
                    key={q.cmd}
                    onClick={() => { setCmd(q.cmd); inputRef.current?.focus(); }}
                    className="px-2.5 py-0.5 text-[10px] font-mono rounded-md bg-[#1e1e35] text-slate-500 hover:bg-[#2d2d50] hover:text-slate-300 border border-[#2d2d4e] transition-all"
                  >
                    {q.label}
                  </button>
                ))}
              </div>

              {/* Input row */}
              <div className="flex items-center gap-3">
                <div className="flex-1 flex items-center gap-2 bg-[#0a0a14] border border-[#252540] rounded-xl px-4 py-2.5 focus-within:border-[#7c8cf8]/40 transition-colors">
                  <span className="text-[#5eead4] font-mono text-[13px] flex-shrink-0">$</span>
                  <input
                    ref={inputRef}
                    value={cmd}
                    onChange={(e) => setCmd(e.target.value)}
                    onKeyDown={handleKeyDown}
                    disabled={running}
                    placeholder={running ? "running…" : "diagnostic command (read-only)"}
                    className="flex-1 bg-transparent text-slate-200 font-mono text-[13px] outline-none placeholder:text-slate-700 disabled:opacity-50 caret-[#7c8cf8]"
                    autoFocus
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>

                <button
                  onClick={runBroadcast}
                  disabled={!cmd.trim() || !selectedCount || running}
                  className={clsx(
                    "flex items-center gap-2 px-4 py-2.5 rounded-xl text-[13px] font-semibold transition-all",
                    "bg-[#7c8cf8] text-white hover:bg-[#6b7ce8] disabled:opacity-40 disabled:cursor-not-allowed",
                    "shadow-lg shadow-[#7c8cf8]/20"
                  )}
                >
                  {running
                    ? <Loader2 size={14} className="animate-spin" />
                    : <Play size={14} />}
                  {running ? "Running…" : `Run on ${selectedCount}`}
                </button>
              </div>
            </div>

            {/* Results */}
            <div className="flex-1 overflow-y-auto">
              {/* Summary bar */}
              {hasRun && (
                <div className="flex items-center gap-4 px-5 py-2.5 border-b border-[#1e1e35] bg-[#0a0a14] text-[11px]">
                  <span className="text-slate-500 font-mono truncate max-w-[200px]">{cmd}</span>
                  <div className="flex items-center gap-3 ml-auto flex-shrink-0">
                    {statusCounts.running > 0 && (
                      <span className="text-[#7c8cf8] flex items-center gap-1">
                        <Loader2 size={10} className="animate-spin" />
                        {statusCounts.running} running
                      </span>
                    )}
                    {statusCounts.ok > 0 && (
                      <span className="text-emerald-400 flex items-center gap-1">
                        <CheckCircle2 size={10} />
                        {statusCounts.ok} ok
                      </span>
                    )}
                    {statusCounts.err > 0 && (
                      <span className="text-red-400 flex items-center gap-1">
                        <XCircle size={10} />
                        {statusCounts.err} failed
                      </span>
                    )}
                  </div>
                </div>
              )}

              {!hasRun && (
                <div className="flex flex-col items-center justify-center h-full gap-3 py-16">
                  <div className="w-12 h-12 rounded-2xl bg-[#1e1e35] flex items-center justify-center">
                    <Zap size={20} className="text-[#3d3d5e]" />
                  </div>
                  <p className="text-[13px] text-slate-600">Select nodes and enter a command to broadcast</p>
                  <p className="text-[11px] text-slate-700">Results will appear here as agents respond</p>
                </div>
              )}

              {sortedResults.map((result) => {
                const meta = STATUS_META[result.status];
                const expanded = expandedIds.has(result.node_id);
                const hasOutput = result.output.trim().length > 0;

                return (
                  <div
                    key={result.node_id}
                    className={clsx(
                      "border-b border-[#1a1a2e] transition-colors",
                      result.status === "error" || result.status === "timeout"
                        ? "bg-red-500/5"
                        : result.status === "success"
                        ? "bg-emerald-500/[0.02]"
                        : ""
                    )}
                  >
                    {/* Row header */}
                    <div
                      className={clsx(
                        "flex items-center gap-3 px-5 py-3",
                        hasOutput && "cursor-pointer hover:bg-white/[0.02]"
                      )}
                      onClick={() => hasOutput && toggleExpand(result.node_id)}
                    >
                      {/* Status icon */}
                      <span className={clsx("flex-shrink-0", meta.color)}>
                        {meta.icon ?? <span className="w-3 h-3 rounded-full border border-[#3d3d5e] block" />}
                      </span>

                      {/* Node name */}
                      <span className="font-mono text-[13px] text-slate-300 flex-shrink-0 w-[160px] truncate">
                        {result.node_name}
                      </span>

                      {/* Status label */}
                      <span className={clsx("text-[11px] font-medium", meta.color)}>
                        {meta.label}
                      </span>

                      {/* Output preview / duration */}
                      {result.status !== "running" && result.status !== "idle" && (
                        <>
                          {result.output && !expanded && (
                            <span className="flex-1 text-[11px] text-slate-600 font-mono truncate min-w-0">
                              {result.output.split("\n")[0]}
                            </span>
                          )}
                          <div className="flex items-center gap-2 ml-auto flex-shrink-0">
                            {result.duration_ms > 0 && (
                              <span className="flex items-center gap-1 text-[10px] text-slate-700">
                                <Clock size={9} />
                                {fmtMs(result.duration_ms)}
                              </span>
                            )}
                            {result.exit_code !== -1 && result.exit_code !== 0 && (
                              <span className="text-[10px] text-red-400/80 font-mono">
                                exit {result.exit_code}
                              </span>
                            )}
                            {hasOutput && (
                              <span className={clsx("text-slate-700", "transition-transform", expanded ? "rotate-180" : "")}>
                                <ChevronDown size={12} />
                              </span>
                            )}
                          </div>
                        </>
                      )}
                    </div>

                    {/* Expanded output */}
                    {expanded && hasOutput && (
                      <div className="px-5 pb-3">
                        <div className="relative rounded-lg bg-[#050508] border border-[#1e1e35] overflow-hidden">
                          <div className="absolute top-2 right-2">
                            <CopyBtn text={result.output} />
                          </div>
                          <pre className={clsx(
                            "font-mono text-[11px] leading-relaxed px-4 py-3 overflow-x-auto max-h-64 whitespace-pre-wrap break-all",
                            result.status === "error" || result.status === "timeout"
                              ? "text-red-300/80"
                              : "text-slate-400"
                          )}>
                            {result.output}
                          </pre>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
