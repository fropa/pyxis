/**
 * Kubernetes Cluster Browser
 * Displays live cluster state pushed by the agent every 30s.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Box, RefreshCw, Server, Layers, Package, Circle,
  CheckCircle2, XCircle, AlertTriangle, Clock, Cpu, HardDrive,
} from "lucide-react";
import { api } from "../api/client";
import clsx from "clsx";

// ── K8s data helpers ──────────────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type KObj = Record<string, any>;

function getMeta(obj: KObj) {
  return (obj.metadata ?? {}) as KObj;
}
function getStatus(obj: KObj) {
  return (obj.status ?? {}) as KObj;
}
function getSpec(obj: KObj) {
  return (obj.spec ?? {}) as KObj;
}

function nodeReady(node: KObj): boolean {
  const conds: KObj[] = getStatus(node).conditions ?? [];
  return conds.some((c) => c.type === "Ready" && c.status === "True");
}

function nodeRoles(node: KObj): string {
  const labels: Record<string, string> = getMeta(node).labels ?? {};
  const roles = Object.keys(labels)
    .filter((k) => k.startsWith("node-role.kubernetes.io/"))
    .map((k) => k.replace("node-role.kubernetes.io/", ""));
  return roles.length ? roles.join(", ") : "worker";
}

function podPhase(pod: KObj): string {
  const phase = getStatus(pod).phase ?? "Unknown";
  // Detect CrashLoopBackOff / OOMKilled from container statuses
  const containerStatuses: KObj[] = getStatus(pod).containerStatuses ?? [];
  for (const cs of containerStatuses) {
    const waiting = cs.state?.waiting;
    if (waiting?.reason) return waiting.reason;
  }
  return phase;
}

function podRestarts(pod: KObj): number {
  const cs: KObj[] = getStatus(pod).containerStatuses ?? [];
  return cs.reduce((s, c) => s + (c.restartCount ?? 0), 0);
}

function podReady(pod: KObj): string {
  const cs: KObj[] = getStatus(pod).containerStatuses ?? [];
  const ready = cs.filter((c) => c.ready).length;
  return cs.length ? `${ready}/${cs.length}` : "0/0";
}

function getAge(ts: string): string {
  if (!ts) return "—";
  const secs = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
  return `${Math.floor(secs / 86400)}d`;
}

function fmtMemory(raw: string): string {
  if (!raw) return "—";
  // Ki → GiB
  if (raw.endsWith("Ki")) {
    const gi = parseInt(raw) / (1024 * 1024);
    return gi >= 1 ? `${gi.toFixed(0)} GiB` : `${(gi * 1024).toFixed(0)} MiB`;
  }
  return raw;
}

// ── Status badge ──────────────────────────────────────────────────────────────

const PHASE_STYLE: Record<string, string> = {
  Running:            "bg-emerald-500/15 text-emerald-400",
  Succeeded:          "bg-slate-500/15 text-slate-400",
  Pending:            "bg-yellow-500/15 text-yellow-400",
  Failed:             "bg-red-500/15 text-red-400",
  Unknown:            "bg-slate-500/15 text-slate-500",
  CrashLoopBackOff:   "bg-red-500/15 text-red-400",
  OOMKilled:          "bg-red-500/15 text-red-400",
  Error:              "bg-red-500/15 text-red-400",
  ImagePullBackOff:   "bg-orange-500/15 text-orange-400",
  Terminating:        "bg-slate-500/15 text-slate-400",
};

function PhaseBadge({ phase }: { phase: string }) {
  return (
    <span className={clsx(
      "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium",
      PHASE_STYLE[phase] ?? "bg-slate-500/15 text-slate-400"
    )}>
      {phase}
    </span>
  );
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

type TabId = "nodes" | "pods" | "workloads";

const TABS: { id: TabId; label: string; icon: React.ElementType }[] = [
  { id: "nodes",     label: "Nodes",     icon: Server },
  { id: "pods",      label: "Pods",      icon: Package },
  { id: "workloads", label: "Workloads", icon: Layers },
];

// ── Component ─────────────────────────────────────────────────────────────────

export default function K8sView() {
  const [tab, setTab] = useState<TabId>("nodes");
  const [nsFilter, setNsFilter] = useState<string>("all");

  const { data, isLoading, isError, refetch, isFetching, dataUpdatedAt } = useQuery({
    queryKey: ["k8s-state"],
    queryFn: api.k8s.state,
    refetchInterval: 30_000,
  });

  const hasData = data && (
    data.nodes.length > 0 || data.pods.length > 0 || data.deployments.length > 0
  );

  // Derived counts
  const readyNodes   = data?.nodes.filter(nodeReady).length ?? 0;
  const totalNodes   = data?.nodes.length ?? 0;
  const totalPods    = data?.pods.length ?? 0;
  const runningPods  = data?.pods.filter((p) => podPhase(p) === "Running").length ?? 0;
  const failedPods   = data?.pods.filter((p) => {
    const ph = podPhase(p);
    return ph === "Failed" || ph === "CrashLoopBackOff" || ph === "OOMKilled" || ph === "Error";
  }).length ?? 0;
  const pendingPods  = data?.pods.filter((p) => podPhase(p) === "Pending").length ?? 0;

  // Namespace list for filter
  const namespaces = Array.from(new Set(
    (data?.pods ?? []).map((p) => getMeta(p).namespace).filter(Boolean)
  )).sort();

  // Filtered pods / deployments
  const filteredPods = nsFilter === "all"
    ? (data?.pods ?? [])
    : (data?.pods ?? []).filter((p) => getMeta(p).namespace === nsFilter);

  const filteredDeployments = nsFilter === "all"
    ? (data?.deployments ?? [])
    : (data?.deployments ?? []).filter((d) => getMeta(d).namespace === nsFilter);

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <div className="h-full flex flex-col overflow-hidden">

      {/* ── Header ── */}
      <div className="flex-shrink-0 flex items-center justify-between px-6 py-4 border-b border-border bg-surface">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center">
            <Box size={15} className="text-accent-text" />
          </div>
          <div>
            <h1 className="text-[14px] font-semibold text-text-1">Kubernetes</h1>
            <p className="text-[12px] text-text-4 mt-0.5">
              {hasData
                ? `${totalNodes} nodes · ${totalPods} pods`
                : "No cluster data — install agent with k8s source"}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="flex items-center gap-1.5 text-[11px] text-text-4">
              <Clock size={11} />
              Updated {lastUpdated}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] font-medium bg-raised border border-border rounded-lg hover:border-border-strong transition-all text-text-2"
          >
            <RefreshCw size={11} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {/* ── Overview cards ── */}
      {hasData && (
        <div className="flex-shrink-0 grid grid-cols-4 gap-4 px-6 py-4 border-b border-border bg-bg">
          <StatCard
            label="Cluster Nodes"
            value={`${readyNodes}/${totalNodes}`}
            sub={readyNodes === totalNodes ? "all ready" : `${totalNodes - readyNodes} not ready`}
            ok={readyNodes === totalNodes}
            icon={Server}
          />
          <StatCard
            label="Running Pods"
            value={String(runningPods)}
            sub={`of ${totalPods} total`}
            ok={failedPods === 0}
            icon={Package}
          />
          {failedPods > 0 ? (
            <StatCard
              label="Failed / Crashing"
              value={String(failedPods)}
              sub="needs attention"
              ok={false}
              icon={XCircle}
            />
          ) : (
            <StatCard
              label="Pending"
              value={String(pendingPods)}
              sub={pendingPods > 0 ? "scheduling…" : "none"}
              ok={pendingPods === 0}
              icon={Circle}
            />
          )}
          <StatCard
            label="Deployments"
            value={String(data?.deployments.length ?? 0)}
            sub={`${namespaces.length} namespaces`}
            ok={true}
            icon={Layers}
          />
        </div>
      )}

      {/* ── No data state ── */}
      {!isLoading && !hasData && (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8">
          <div className="w-16 h-16 rounded-2xl bg-raised border border-border flex items-center justify-center">
            <Box size={28} className="text-text-4" />
          </div>
          <div>
            <p className="text-[15px] font-semibold text-text-1 mb-1">No cluster data yet</p>
            <p className="text-[13px] text-text-3 max-w-sm leading-relaxed">
              Install the Pyxis agent on a node with <code className="font-mono bg-raised px-1.5 py-0.5 rounded text-accent-text text-[12px]">k8s</code> source enabled.
              The agent needs <code className="font-mono bg-raised px-1.5 py-0.5 rounded text-accent-text text-[12px]">kubectl</code> access to the cluster.
            </p>
          </div>
          <div className="bg-[#0d0d18] rounded-xl border border-[#252540] px-5 py-3 text-left max-w-lg w-full">
            <p className="text-[11px] text-slate-600 mb-2 font-mono">Install with k8s source:</p>
            <pre className="text-[12px] text-emerald-400 font-mono whitespace-pre-wrap break-all">
              {`curl -fsSL "http://your-pyxis/install/linux?sources=k8s,syslog&..." | bash`}
            </pre>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="flex-1 flex items-center justify-center gap-3">
          <RefreshCw size={16} className="animate-spin text-text-4" />
          <span className="text-[13px] text-text-3">Loading cluster state…</span>
        </div>
      )}

      {isError && (
        <div className="flex-1 flex items-center justify-center">
          <p className="text-[13px] text-danger">Failed to load cluster state</p>
        </div>
      )}

      {/* ── Tabs + content ── */}
      {hasData && (
        <div className="flex-1 flex flex-col min-h-0">

          {/* Tab bar + namespace filter */}
          <div className="flex-shrink-0 flex items-center gap-1 px-6 border-b border-border bg-surface">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    "flex items-center gap-1.5 px-4 py-3 text-[13px] font-medium border-b-2 transition-all",
                    tab === t.id
                      ? "border-accent text-accent-text"
                      : "border-transparent text-text-3 hover:text-text-1"
                  )}
                >
                  <Icon size={13} />
                  {t.label}
                  {t.id === "nodes" && <CountBadge n={totalNodes} />}
                  {t.id === "pods" && <CountBadge n={filteredPods.length} />}
                  {t.id === "workloads" && <CountBadge n={filteredDeployments.length} />}
                </button>
              );
            })}

            {/* Namespace filter */}
            {(tab === "pods" || tab === "workloads") && namespaces.length > 0 && (
              <div className="ml-auto flex items-center gap-2 py-2">
                <span className="text-[11px] text-text-4">Namespace:</span>
                <select
                  value={nsFilter}
                  onChange={(e) => setNsFilter(e.target.value)}
                  className="text-[12px] bg-raised border border-border rounded-lg px-2 py-1 text-text-1 focus:outline-none focus:border-accent/50"
                >
                  <option value="all">All</option>
                  {namespaces.map((ns) => (
                    <option key={ns} value={ns}>{ns}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-auto">

            {/* ── Nodes tab ── */}
            {tab === "nodes" && (
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left border-b border-border bg-surface sticky top-0 z-10">
                    <Th>Name</Th>
                    <Th>Status</Th>
                    <Th>Roles</Th>
                    <Th>Version</Th>
                    <Th>CPU</Th>
                    <Th>Memory</Th>
                    <Th>OS</Th>
                    <Th>Age</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.nodes.map((node) => {
                    const meta = getMeta(node);
                    const info = getStatus(node).nodeInfo ?? {};
                    const cap  = getStatus(node).capacity ?? {};
                    const ready = nodeReady(node);
                    return (
                      <tr key={meta.name} className="border-b border-border/50 hover:bg-raised/50 transition-colors">
                        <Td>
                          <div className="flex items-center gap-2">
                            {ready
                              ? <CheckCircle2 size={12} className="text-success flex-shrink-0" />
                              : <XCircle size={12} className="text-danger flex-shrink-0" />}
                            <span className="font-mono font-medium text-text-1">{meta.name}</span>
                          </div>
                        </Td>
                        <Td>
                          <span className={clsx(
                            "px-2 py-0.5 rounded-full text-[11px] font-medium",
                            ready ? "bg-success/10 text-success-text" : "bg-danger/10 text-danger"
                          )}>
                            {ready ? "Ready" : "NotReady"}
                          </span>
                        </Td>
                        <Td className="text-text-3">{nodeRoles(node)}</Td>
                        <Td className="font-mono text-text-3">{info.kubeletVersion ?? "—"}</Td>
                        <Td className="text-text-2">
                          <div className="flex items-center gap-1">
                            <Cpu size={10} className="text-text-4" />
                            {cap.cpu ?? "—"}
                          </div>
                        </Td>
                        <Td className="text-text-2">
                          <div className="flex items-center gap-1">
                            <HardDrive size={10} className="text-text-4" />
                            {fmtMemory(cap.memory ?? "")}
                          </div>
                        </Td>
                        <Td className="text-text-4 max-w-[160px] truncate">{info.osImage ?? "—"}</Td>
                        <Td className="text-text-4">{getAge(meta.creationTimestamp)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}

            {/* ── Pods tab ── */}
            {tab === "pods" && (
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left border-b border-border bg-surface sticky top-0 z-10">
                    <Th>Name</Th>
                    <Th>Namespace</Th>
                    <Th>Status</Th>
                    <Th>Ready</Th>
                    <Th>Restarts</Th>
                    <Th>Node</Th>
                    <Th>Age</Th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPods.map((pod) => {
                    const meta     = getMeta(pod);
                    const phase    = podPhase(pod);
                    const restarts = podRestarts(pod);
                    const node     = getSpec(pod).nodeName ?? "—";
                    return (
                      <tr key={`${meta.namespace}/${meta.name}`} className="border-b border-border/50 hover:bg-raised/50 transition-colors">
                        <Td>
                          <span className="font-mono text-text-1">{meta.name}</span>
                        </Td>
                        <Td>
                          <span className="px-1.5 py-0.5 rounded bg-raised border border-border text-[10px] text-text-3 font-mono">
                            {meta.namespace}
                          </span>
                        </Td>
                        <Td><PhaseBadge phase={phase} /></Td>
                        <Td className="text-text-3">{podReady(pod)}</Td>
                        <Td>
                          <span className={restarts > 0 ? "text-warning font-semibold" : "text-text-4"}>
                            {restarts > 0 && <AlertTriangle size={10} className="inline mr-1 mb-0.5" />}
                            {restarts}
                          </span>
                        </Td>
                        <Td className="font-mono text-text-4 max-w-[160px] truncate">{node}</Td>
                        <Td className="text-text-4">{getAge(meta.creationTimestamp)}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}

            {/* ── Workloads tab (Deployments) ── */}
            {tab === "workloads" && (
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left border-b border-border bg-surface sticky top-0 z-10">
                    <Th>Name</Th>
                    <Th>Namespace</Th>
                    <Th>Ready</Th>
                    <Th>Up-to-date</Th>
                    <Th>Available</Th>
                    <Th>Age</Th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDeployments.map((dep) => {
                    const meta   = getMeta(dep);
                    const status = getStatus(dep);
                    const spec   = getSpec(dep);
                    const desired   = spec.replicas ?? 0;
                    const ready_    = status.readyReplicas ?? 0;
                    const upToDate  = status.updatedReplicas ?? 0;
                    const available = status.availableReplicas ?? 0;
                    const healthy   = ready_ >= desired;
                    return (
                      <tr key={`${meta.namespace}/${meta.name}`} className="border-b border-border/50 hover:bg-raised/50 transition-colors">
                        <Td>
                          <div className="flex items-center gap-2">
                            {healthy
                              ? <CheckCircle2 size={12} className="text-success flex-shrink-0" />
                              : <AlertTriangle size={12} className="text-warning flex-shrink-0" />}
                            <span className="font-mono text-text-1">{meta.name}</span>
                          </div>
                        </Td>
                        <Td>
                          <span className="px-1.5 py-0.5 rounded bg-raised border border-border text-[10px] text-text-3 font-mono">
                            {meta.namespace}
                          </span>
                        </Td>
                        <Td>
                          <span className={clsx(
                            "font-semibold",
                            healthy ? "text-success-text" : "text-warning"
                          )}>
                            {ready_}/{desired}
                          </span>
                        </Td>
                        <Td className="text-text-2">{upToDate}</Td>
                        <Td className="text-text-2">{available}</Td>
                        <Td className="text-text-4">{getAge(meta.creationTimestamp)}</Td>
                      </tr>
                    );
                  })}
                  {filteredDeployments.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-6 py-8 text-center text-text-4 text-[12px]">
                        No deployments in {nsFilter === "all" ? "any namespace" : `namespace "${nsFilter}"`}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, ok, icon: Icon,
}: {
  label: string;
  value: string;
  sub: string;
  ok: boolean;
  icon: React.ElementType;
}) {
  return (
    <div className="bg-surface border border-border rounded-xl p-4 flex items-start gap-3">
      <div className={clsx(
        "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5",
        ok ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
      )}>
        <Icon size={15} />
      </div>
      <div>
        <p className="text-[11px] text-text-4 font-medium">{label}</p>
        <p className="text-[18px] font-bold text-text-1 leading-tight">{value}</p>
        <p className="text-[11px] text-text-4 mt-0.5">{sub}</p>
      </div>
    </div>
  );
}

function CountBadge({ n }: { n: number }) {
  return (
    <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded-full bg-raised border border-border text-text-4 tabular-nums">
      {n}
    </span>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-4 py-3 text-[11px] font-semibold text-text-3 whitespace-nowrap">
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <td className={clsx("px-4 py-3 text-text-2 whitespace-nowrap", className)}>
      {children}
    </td>
  );
}
