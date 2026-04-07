import { useState, useRef, useEffect, useCallback } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  X, Loader2, Terminal, ChevronDown, ChevronRight, Trash2,
  ScrollText, TerminalSquare, CheckCircle2, XCircle, Clock,
  Copy, Check, Eraser, Zap, ChevronUp, Filter, RefreshCw,
} from "lucide-react";
import { api, getErrorMessage } from "../../api/client";
import type { TopologyNode, NodeLogEntry } from "../../api/client";
import clsx from "clsx";

// ── Helpers ───────────────────────────────────────────────────────────────────

const LEVEL_COLOR: Record<string, string> = {
  critical: "text-red-400", error: "text-red-400",
  warning: "text-yellow-400", warn: "text-yellow-400",
  info: "text-slate-300", debug: "text-slate-500",
};
const LEVEL_BG: Record<string, string> = {
  critical: "bg-red-500/20 border-l-2 border-red-500/60",
  error: "bg-red-500/10 border-l-2 border-red-500/40",
  warning: "bg-yellow-500/10 border-l-2 border-yellow-500/40",
};
const SOURCE_ICON: Record<string, string> = {
  syslog: "SYS", k8s_event: "K8S", ci_pipeline: "CI", app_log: "APP", audit_log: "AUD",
};
const QUICK_CMDS = [
  { label: "uptime",   cmd: "uptime" },
  { label: "df -h",    cmd: "df -h" },
  { label: "free -h",  cmd: "free -h" },
  { label: "top snap", cmd: "top -bn1 | head -20" },
  { label: "ps aux",   cmd: "ps aux --sort=-%cpu | head -15" },
  { label: "netstat",  cmd: "ss -tulnp" },
  { label: "dmesg",    cmd: "dmesg | tail -20" },
  { label: "who",      cmd: "who; last | head -5" },
];

function fmtTs(ts: string) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDate(ts: string) {
  const d = new Date(ts);
  return d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
    d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

interface HistoryEntry {
  id: number;
  cmd: string;
  output: string;
  exit_code: number;
  duration_ms: number;
  ts: Date;
}

type Tab = "logs" | "console";

// ── Copy button ───────────────────────────────────────────────────────────────

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
      title="Copy"
    >
      {copied ? <Check size={11} className="text-green-400" /> : <Copy size={11} />}
    </button>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

interface Props {
  node: TopologyNode;
  onClose: () => void;
}

export default function NodeLogsPanel({ node, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("logs");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [cmd, setCmd] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [navIdx, setNavIdx] = useState(-1);
  const [sourceFilter, setSourceFilter] = useState<string | null>(null);

  // Accumulated log entries (merged across pagination pages)
  const [allLogs, setAllLogs] = useState<NodeLogEntry[]>([]);
  const [hasOlder, setHasOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loadingLogs, setLoadingLogs] = useState(true);
  const [logsError, setLogsError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState(Date.now());

  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(0);
  const qc = useQueryClient();

  // ── Fetch latest logs ─────────────────────────────────────────────────────

  const fetchLogs = useCallback(async (replace = true) => {
    try {
      setLoadingLogs(replace);
      const data = await api.topology.nodeLogs(node.id, {
        limit: 100,
        source: sourceFilter ?? undefined,
      });
      if (replace) {
        setAllLogs(flattenLogs(data.by_source));
      } else {
        setAllLogs((prev) => dedupe([...flattenLogs(data.by_source), ...prev]));
      }
      setHasOlder(data.has_older);
      setLogsError(null);
    } catch (err) {
      setLogsError(getErrorMessage(err));
    } finally {
      setLoadingLogs(false);
    }
  }, [node.id, sourceFilter]);

  useEffect(() => {
    fetchLogs(true);
  }, [fetchLogs, lastRefresh]);

  // Auto-refresh every 10s
  useEffect(() => {
    const t = setInterval(() => setLastRefresh(Date.now()), 10_000);
    return () => clearInterval(t);
  }, []);

  // ── Load older logs ────────────────────────────────────────────────────────

  const loadOlder = useCallback(async () => {
    const oldest = allLogs[0];
    if (!oldest || loadingOlder) return;
    try {
      setLoadingOlder(true);
      const data = await api.topology.nodeLogs(node.id, {
        limit: 100,
        before: oldest.ts,
        source: sourceFilter ?? undefined,
      });
      const older = flattenLogs(data.by_source);
      setAllLogs((prev) => dedupe([...older, ...prev]));
      setHasOlder(data.has_older);
    } catch (err) {
      // ignore — user can retry
    } finally {
      setLoadingOlder(false);
    }
  }, [node.id, allLogs, loadingOlder, sourceFilter]);

  // ── Mutations ──────────────────────────────────────────────────────────────

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
      setHistory((h) => [...h, {
        id: nextId.current++, cmd: command,
        output: data.output, exit_code: data.exit_code,
        duration_ms: data.duration_ms, ts: new Date(),
      }]);
      setCmd(""); setNavIdx(-1);
    },
    onError: (err, command) => {
      setHistory((h) => [...h, {
        id: nextId.current++, cmd: command,
        output: getErrorMessage(err), exit_code: -1,
        duration_ms: 0, ts: new Date(),
      }]);
      setCmd(""); setNavIdx(-1);
    },
  });

  // ── Derived log state ──────────────────────────────────────────────────────

  const availableSources = Array.from(new Set(allLogs.map((l) => l.source))).sort();
  const filteredLogs = sourceFilter
    ? allLogs.filter((l) => l.source === sourceFilter)
    : allLogs;

  // Group by source for display
  const bySource: Record<string, NodeLogEntry[]> = {};
  for (const entry of filteredLogs) {
    (bySource[entry.source] ??= []).push(entry);
  }

  // ── Effects ────────────────────────────────────────────────────────────────

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, execMutation.isPending]);

  useEffect(() => {
    if (tab === "console") setTimeout(() => inputRef.current?.focus(), 60);
  }, [tab]);

  // ── Helpers ────────────────────────────────────────────────────────────────

  const runCmd = useCallback((command: string) => {
    const c = command.trim();
    if (!c || execMutation.isPending) return;
    execMutation.mutate(c);
  }, [execMutation]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") { runCmd(cmd); return; }
    const cmds = history.map((h) => h.cmd);
    if (e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.min(navIdx + 1, cmds.length - 1);
      setNavIdx(next);
      setCmd(cmds[cmds.length - 1 - next] ?? "");
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = navIdx - 1;
      if (next < 0) { setNavIdx(-1); setCmd(""); }
      else { setNavIdx(next); setCmd(cmds[cmds.length - 1 - next] ?? ""); }
    }
    if (e.key === "l" && e.ctrlKey) { e.preventDefault(); setHistory([]); }
    if (e.key === "c" && e.ctrlKey) { setCmd(""); setNavIdx(-1); }
  }

  const ip = node.metadata?.ip_address as string | undefined;
  const promptHost = node.name;

  return (
    <div className="fixed inset-y-0 right-0 w-[640px] bg-[#0d0d18] border-l border-[#252540] flex flex-col z-50 shadow-2xl animate-slide-in">

      {/* ── Header ── */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-[#252540] flex-shrink-0 bg-[#111124]">
        <div className="flex items-center gap-1.5">
          <button onClick={onClose} className="w-3 h-3 rounded-full bg-[#ff5f57] hover:brightness-110 transition-all" title="Close" />
          <div className="w-3 h-3 rounded-full bg-[#febc2e]" />
          <div className="w-3 h-3 rounded-full bg-[#28c840]" />
        </div>

        <div className="flex-1 min-w-0 ml-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-slate-200 truncate">{node.name}</span>
            {ip && <span className="text-[11px] font-mono text-slate-500">{ip}</span>}
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#252540] text-slate-500 font-mono">{node.kind}</span>
            <span className={clsx(
              "text-[10px] px-1.5 py-0.5 rounded font-medium",
              node.status === "healthy" ? "bg-emerald-500/15 text-emerald-400" :
              node.status === "down" ? "bg-red-500/15 text-red-400" :
              "bg-yellow-500/15 text-yellow-400"
            )}>{node.status}</span>
          </div>
        </div>

        {confirmDelete ? (
          <div className="flex items-center gap-1.5 animate-fade-in">
            <span className="text-[11px] text-red-400">Remove?</span>
            <button onClick={() => deleteMutation.mutate()} disabled={deleteMutation.isPending}
              className="px-2 py-0.5 text-[11px] font-semibold rounded-md bg-red-600/80 text-white hover:bg-red-500 transition-colors">
              {deleteMutation.isPending ? "…" : "Yes"}
            </button>
            <button onClick={() => setConfirmDelete(false)}
              className="px-2 py-0.5 text-[11px] rounded-md text-slate-400 hover:text-white hover:bg-white/5 transition-colors">
              No
            </button>
          </div>
        ) : (
          <button onClick={() => setConfirmDelete(true)}
            className="p-1.5 rounded-lg text-slate-700 hover:text-red-400 hover:bg-white/5 transition-colors" title="Remove from topology">
            <Trash2 size={13} />
          </button>
        )}
      </div>

      {/* ── Tabs ── */}
      <div className="flex border-b border-[#252540] flex-shrink-0 bg-[#111124]">
        {(["logs", "console"] as Tab[]).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx(
              "flex items-center gap-1.5 px-5 py-2.5 text-[12px] font-medium border-b-2 transition-all",
              tab === t ? "border-[#7c8cf8] text-[#a5b4fc]" : "border-transparent text-slate-600 hover:text-slate-400"
            )}>
            {t === "logs" ? <ScrollText size={12} /> : <TerminalSquare size={12} />}
            {t === "logs" ? "Logs" : "Console"}
            {t === "logs" && filteredLogs.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 text-[9px] rounded-full bg-[#252540] text-slate-500 tabular-nums">
                {filteredLogs.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Logs tab ── */}
      {tab === "logs" && (
        <>
          {/* Filters + refresh */}
          <div className="flex-shrink-0 flex items-center gap-2 px-4 py-2 border-b border-[#1e1e35] bg-[#0f0f1e] overflow-x-auto scrollbar-none">
            <Filter size={10} className="text-slate-700 flex-shrink-0" />
            <button
              onClick={() => setSourceFilter(null)}
              className={clsx(
                "flex-shrink-0 px-2 py-0.5 text-[10px] font-mono rounded transition-all",
                sourceFilter === null
                  ? "bg-[#7c8cf8]/20 text-[#a5b4fc] border border-[#7c8cf8]/30"
                  : "text-slate-600 hover:text-slate-400 border border-transparent"
              )}
            >All</button>
            {availableSources.map((src) => (
              <button
                key={src}
                onClick={() => setSourceFilter(src === sourceFilter ? null : src)}
                className={clsx(
                  "flex-shrink-0 px-2 py-0.5 text-[10px] font-mono rounded transition-all",
                  sourceFilter === src
                    ? "bg-[#7c8cf8]/20 text-[#a5b4fc] border border-[#7c8cf8]/30"
                    : "text-slate-600 hover:text-slate-400 border border-transparent"
                )}
              >
                {SOURCE_ICON[src] ?? src}
              </button>
            ))}
            <button
              onClick={() => setLastRefresh(Date.now())}
              className="ml-auto flex-shrink-0 p-1 rounded text-slate-700 hover:text-slate-400 transition-colors"
              title="Refresh"
            >
              <RefreshCw size={11} className={loadingLogs ? "animate-spin" : ""} />
            </button>
          </div>

          <div className="flex-1 overflow-y-auto">

            {/* Load older button */}
            {hasOlder && (
              <div className="flex justify-center py-3 border-b border-[#1a1a2e]">
                <button
                  onClick={loadOlder}
                  disabled={loadingOlder}
                  className="flex items-center gap-2 px-4 py-1.5 text-[11px] rounded-full border border-[#2d2d50] text-slate-400 hover:border-[#7c8cf8]/50 hover:text-[#a5b4fc] transition-all disabled:opacity-40"
                >
                  {loadingOlder
                    ? <Loader2 size={11} className="animate-spin" />
                    : <ChevronUp size={11} />}
                  Load older logs
                </button>
              </div>
            )}

            {loadingLogs && (
              <div className="flex items-center gap-2 p-5 text-slate-500 text-[12px]">
                <Loader2 size={13} className="animate-spin" /> Loading logs…
              </div>
            )}
            {logsError && (
              <p className="p-5 text-[12px] text-red-400">{logsError}</p>
            )}
            {!loadingLogs && !logsError && filteredLogs.length === 0 && (
              <div className="p-8 text-center flex flex-col items-center gap-3">
                <Terminal size={28} className="text-slate-800" />
                <p className="text-[13px] text-slate-500">No logs received yet</p>
                <p className="text-[11px] text-slate-700 leading-relaxed max-w-[280px]">
                  The agent is running but hasn't sent logs yet, or they haven't been indexed.<br />
                  Try restarting the pyxis-agent service on this node.
                </p>
                <code className="text-[10px] text-slate-600 bg-[#0a0a14] px-3 py-1.5 rounded-lg border border-[#1e1e35] font-mono">
                  systemctl restart pyxis-agent
                </code>
              </div>
            )}

            {/* Log entries grouped by source */}
            {Object.entries(bySource).map(([source, entries]) => {
              const abbrev = SOURCE_ICON[source] ?? source.slice(0, 3).toUpperCase();
              const isOpen = !collapsed[source];
              return (
                <div key={source} className="border-b border-[#1a1a2e]">
                  <button
                    onClick={() => setCollapsed((c) => ({ ...c, [source]: !c[source] }))}
                    className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-white/[0.03] transition-colors"
                  >
                    {isOpen ? <ChevronDown size={11} className="text-slate-600" /> : <ChevronRight size={11} className="text-slate-600" />}
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#252540] text-[#7c8cf8] font-mono tracking-wider">{abbrev}</span>
                    <span className="text-[12px] font-medium text-slate-400">{source}</span>
                    <span className="ml-auto text-[10px] text-slate-700 tabular-nums">{entries.length}</span>
                  </button>
                  {isOpen && (
                    <div className="pb-2">
                      {entries.map((e) => (
                        <div
                          key={e.id}
                          className={clsx(
                            "group flex gap-2 text-[11px] font-mono leading-5 px-5 py-0.5 mx-0 hover:bg-white/[0.02]",
                            LEVEL_BG[e.level] ?? ""
                          )}
                        >
                          <span className="text-slate-700 flex-shrink-0 w-[76px] tabular-nums" title={fmtDate(e.ts)}>
                            {fmtTs(e.ts)}
                          </span>
                          <span className={clsx("flex-shrink-0 w-[46px]", LEVEL_COLOR[e.level] ?? "text-slate-400")}>
                            {e.level.slice(0, 4).toUpperCase()}
                          </span>
                          <span className="text-slate-400 break-all flex-1">{e.message}</span>
                          <span className="opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                            <CopyBtn text={e.message} />
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Status bar */}
          <div className="flex-shrink-0 flex items-center gap-3 px-4 py-1.5 border-t border-[#252540] text-[10px] text-slate-700 bg-[#111124]">
            <span className="tabular-nums">{filteredLogs.length} lines</span>
            {sourceFilter && <span className="text-[#7c8cf8]/60">· filtered: {sourceFilter}</span>}
            <span className="ml-auto">auto-refresh 10s</span>
          </div>
        </>
      )}

      {/* ── Console tab ── */}
      {tab === "console" && (
        <>
          {/* Quick commands */}
          <div className="flex-shrink-0 flex items-center gap-1.5 px-4 py-2 border-b border-[#252540] bg-[#0f0f1e] overflow-x-auto scrollbar-none">
            <Zap size={10} className="text-slate-700 flex-shrink-0" />
            {QUICK_CMDS.map((q) => (
              <button key={q.cmd} onClick={() => runCmd(q.cmd)} disabled={execMutation.isPending}
                className="flex-shrink-0 px-2.5 py-1 text-[10px] font-mono rounded-md bg-[#1e1e35] text-slate-400 hover:bg-[#2d2d50] hover:text-slate-200 border border-[#2d2d4e] transition-all disabled:opacity-40">
                {q.label}
              </button>
            ))}
          </div>

          {/* Output area */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 scroll-smooth">
            {history.length === 0 && !execMutation.isPending && (
              <div className="flex flex-col items-center justify-center h-full text-center py-12 gap-3">
                <TerminalSquare size={32} className="text-slate-800" />
                <p className="text-[13px] text-slate-600">Connected to <span className="text-slate-400 font-mono">{node.name}</span></p>
                <p className="text-[11px] text-slate-700">Type a command or pick one above</p>
                <div className="flex gap-2 mt-1 text-[10px] text-slate-800">
                  <span>↑↓ history</span><span>·</span><span>Ctrl+L clear</span><span>·</span><span>Ctrl+C cancel</span>
                </div>
              </div>
            )}

            {history.map((h) => (
              <div key={h.id} className="group">
                <div className="flex items-center gap-2 mb-1">
                  <div className="flex items-center gap-1.5 flex-1 min-w-0">
                    <span className="font-mono text-[12px] flex-shrink-0">
                      <span className="text-[#5eead4]">{promptHost}</span>
                      <span className="text-slate-600 mx-0.5">~</span>
                      <span className="text-slate-300">$</span>
                    </span>
                    <span className="text-slate-200 font-mono text-[12px] truncate">{h.cmd}</span>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                    <CopyBtn text={h.output} />
                    <span className="flex items-center gap-1 text-[10px] text-slate-700">
                      <Clock size={9} />{fmtDuration(h.duration_ms)}
                    </span>
                    {h.exit_code === 0
                      ? <CheckCircle2 size={12} className="text-emerald-500" />
                      : <XCircle size={12} className="text-red-400" />}
                    {h.exit_code !== 0 && (
                      <span className="text-[10px] text-red-400 font-mono">{h.exit_code}</span>
                    )}
                  </div>
                </div>
                {h.output && (
                  <pre className={clsx(
                    "font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all",
                    "bg-[#0a0a14] rounded-lg px-3 py-2.5 border border-[#1e1e35]",
                    h.exit_code !== 0 ? "text-red-300/80" : "text-slate-400",
                  )}>
                    {h.output}
                  </pre>
                )}
              </div>
            ))}

            {execMutation.isPending && (
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-[12px]">
                    <span className="text-[#5eead4]">{promptHost}</span>
                    <span className="text-slate-600 mx-0.5">~</span>
                    <span className="text-yellow-400">$</span>
                  </span>
                  <span className="text-slate-200 font-mono text-[12px]">{cmd || "…"}</span>
                </div>
                <div className="flex items-center gap-2 px-3 py-2.5 bg-[#0a0a14] rounded-lg border border-[#1e1e35]">
                  <Loader2 size={11} className="animate-spin text-[#7c8cf8]" />
                  <span className="text-[11px] text-slate-600 font-mono">running…</span>
                </div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input bar */}
          <div className="flex-shrink-0 bg-[#111124] border-t border-[#252540]">
            <div className="flex items-center justify-between px-4 pt-2 pb-1">
              <div className="flex items-center gap-1 text-[10px] text-slate-700">
                <span className="font-mono">{node.name}</span>
                {ip && <><span className="mx-1">·</span><span className="font-mono">{ip}</span></>}
              </div>
              <button onClick={() => setHistory([])}
                className="flex items-center gap-1 text-[10px] text-slate-700 hover:text-slate-400 transition-colors px-1.5 py-0.5 rounded hover:bg-white/5">
                <Eraser size={10} /> clear
              </button>
            </div>

            <div className="flex items-center gap-2 px-4 pb-3">
              <span className="font-mono text-[13px] flex-shrink-0">
                <span className="text-[#5eead4]">{promptHost}</span>
                <span className="text-slate-600 mx-0.5">~</span>
                <span className={clsx("transition-colors", execMutation.isPending ? "text-yellow-400" : "text-slate-300")}>$</span>
              </span>
              <input
                ref={inputRef}
                value={cmd}
                onChange={(e) => { setCmd(e.target.value); setNavIdx(-1); }}
                onKeyDown={handleKeyDown}
                disabled={execMutation.isPending}
                placeholder={execMutation.isPending ? "waiting for result…" : "type a command…"}
                className="flex-1 bg-transparent text-slate-200 font-mono text-[13px] outline-none placeholder:text-slate-700 disabled:opacity-40 caret-[#7c8cf8]"
                autoComplete="off"
                spellCheck={false}
              />
              {execMutation.isPending
                ? <Loader2 size={13} className="text-slate-600 animate-spin flex-shrink-0" />
                : cmd.trim() && (
                  <kbd className="text-[9px] text-slate-700 border border-[#252540] rounded px-1 py-0.5 font-mono flex-shrink-0">↵</kbd>
                )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function flattenLogs(bySource: Record<string, NodeLogEntry[]>): NodeLogEntry[] {
  return Object.values(bySource).flat().sort((a, b) => a.ts < b.ts ? -1 : 1);
}

function dedupe(logs: NodeLogEntry[]): NodeLogEntry[] {
  const seen = new Set<string>();
  return logs.filter((l) => {
    if (seen.has(l.id)) return false;
    seen.add(l.id);
    return true;
  }).sort((a, b) => a.ts < b.ts ? -1 : 1);
}
