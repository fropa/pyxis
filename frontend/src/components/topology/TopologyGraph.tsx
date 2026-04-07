import { useEffect, memo } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  useNodesState,
  useEdgesState,
  BackgroundVariant,
  Handle,
  Position,
  NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";
import type { Topology, TopologyNode } from "../../api/client";
import { useAppStore } from "../../store";
import clsx from "clsx";

// ── Status styles ──────────────────────────────────────────────────────────────

const STATUS_BORDER: Record<string, string> = {
  healthy:  "border-success",
  degraded: "border-warning",
  down:     "border-danger",
  unknown:  "border-slate-300",
};

const STATUS_SHADOW: Record<string, string> = {
  healthy:  "",
  degraded: "shadow-[0_0_12px_rgba(217,119,6,0.25)]",
  down:     "shadow-[0_0_12px_rgba(220,38,38,0.3)]",
  unknown:  "",
};

const STATUS_DOT: Record<string, string> = {
  healthy:  "bg-success",
  degraded: "bg-warning",
  down:     "bg-danger",
  unknown:  "bg-slate-400",
};

const STATUS_DOT_PULSE: Record<string, boolean> = {
  healthy: false, degraded: true, down: true, unknown: false,
};

// ── Kind meta ──────────────────────────────────────────────────────────────────

const KIND_META: Record<string, { abbrev: string; label: string; bg: string; text: string }> = {
  linux_host:     { abbrev: "LX", label: "Linux Host",  bg: "bg-indigo-50",  text: "text-indigo-600" },
  k8s_node:       { abbrev: "KN", label: "K8s Node",    bg: "bg-blue-50",    text: "text-blue-600"   },
  k8s_pod:        { abbrev: "PD", label: "Pod",         bg: "bg-violet-50",  text: "text-violet-600" },
  k8s_service:    { abbrev: "SV", label: "Service",     bg: "bg-amber-50",   text: "text-amber-600"  },
  k8s_deployment: { abbrev: "DP", label: "Deployment",  bg: "bg-cyan-50",    text: "text-cyan-600"   },
  k8s_namespace:  { abbrev: "NS", label: "Namespace",   bg: "bg-slate-100",  text: "text-slate-500"  },
  aws_ec2:        { abbrev: "EC", label: "EC2",         bg: "bg-orange-50",  text: "text-orange-600" },
  aws_rds:        { abbrev: "DB", label: "RDS",         bg: "bg-green-50",   text: "text-green-600"  },
  ci_runner:      { abbrev: "CI", label: "CI Runner",   bg: "bg-pink-50",    text: "text-pink-600"   },
  service:        { abbrev: "SV", label: "Service",     bg: "bg-violet-50",  text: "text-violet-600" },
  unknown:        { abbrev: "?",  label: "Unknown",     bg: "bg-slate-100",  text: "text-slate-400"  },
};

// ── Edge styling by kind ───────────────────────────────────────────────────────

const EDGE_COLOR: Record<string, string> = {
  calls:         "#6366f1",  // indigo — confirmed call chain
  dependency:    "#d97706",  // amber — inferred dependency
  "co-deployed": "#64748b",  // slate — deployment correlation
  "co-occurrence": "#9333ea", // purple — incident co-occurrence
  network:       "#94a3b8",  // default
};

// ── Custom node ────────────────────────────────────────────────────────────────

interface NodeData {
  node: TopologyNode;
  flashing?: boolean;
}

const InfraNode = memo(({ data }: NodeProps<NodeData>) => {
  const { node, flashing } = data;
  const meta = KIND_META[node.kind] ?? KIND_META.unknown;
  const status = node.status ?? "unknown";
  const pulseDot = STATUS_DOT_PULSE[status] ?? false;

  return (
    <>
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: "#cbd5e1", border: "none", width: 6, height: 6 }}
      />
      <div
        className={clsx(
          "w-[160px] bg-white border-2 rounded-xl px-3 py-2.5 transition-all duration-300",
          "shadow-card",
          STATUS_BORDER[status] ?? "border-slate-200",
          STATUS_SHADOW[status],
          flashing && "ring-2 ring-accent/40 ring-offset-2"
        )}
      >
        <div className="flex items-center justify-between mb-1.5">
          <span
            className={clsx(
              "text-[10px] font-bold px-1.5 py-0.5 rounded-md",
              meta.bg,
              meta.text
            )}
          >
            {meta.abbrev}
          </span>
          <span className="relative flex items-center justify-center">
            {pulseDot && (
              <span
                className={clsx(
                  "absolute w-3 h-3 rounded-full opacity-40 status-pulse",
                  STATUS_DOT[status]
                )}
              />
            )}
            <span
              className={clsx(
                "w-2 h-2 rounded-full relative z-10",
                STATUS_DOT[status] ?? "bg-slate-300"
              )}
            />
          </span>
        </div>
        <p className="text-[12px] font-semibold text-slate-800 truncate leading-tight">
          {node.name}
        </p>
        <p className="text-[10px] text-slate-400 mt-0.5">{meta.label}</p>
        {node.metadata?.ip_address && (
          <p className="text-[10px] text-slate-400 font-mono mt-0.5 truncate">
            {node.metadata.ip_address}
          </p>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
        style={{ background: "#cbd5e1", border: "none", width: 6, height: 6 }}
      />
    </>
  );
});

const NODE_TYPES = { infra: InfraNode };

// ── Builders ───────────────────────────────────────────────────────────────────

function buildFlowNodes(nodes: TopologyNode[]): Node[] {
  return nodes.map((n, i) => ({
    id: n.id,
    type: "infra",
    position: { x: (i % 5) * 210, y: Math.floor(i / 5) * 130 },
    data: { node: n, flashing: false },
  }));
}

function buildFlowEdges(edges: Topology["edges"]): Edge[] {
  return edges.map((e) => {
    const color = EDGE_COLOR[e.kind] ?? EDGE_COLOR.network;
    const confidence = e.confidence ?? 0.7;
    // Map confidence [0.5–1.0] → opacity [0.3–1.0]
    const opacity = 0.3 + confidence * 0.7;
    const strokeWidth = confidence >= 0.9 ? 2 : confidence >= 0.7 ? 1.5 : 1;
    const confPct = Math.round(confidence * 100);
    const label = confidence < 0.95 ? `${e.kind} ${confPct}%` : e.kind;

    return {
      id: e.id,
      source: e.source_id,
      target: e.target_id,
      label,
      style: {
        stroke: color,
        strokeWidth,
        opacity,
      },
      labelStyle: { fill: color, fontSize: 9, fontWeight: 500, opacity },
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85, rx: 3 },
      labelBgPadding: [3, 5] as [number, number],
      markerEnd: { type: "arrowclosed" as const, color, width: 14, height: 14 },
    };
  });
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function TopologyGraph({
  topology,
  onNodeSelect,
}: {
  topology: Topology;
  onNodeSelect?: (node: TopologyNode | null) => void;
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const recentEvents = useAppStore((s) => s.recentEvents);

  useEffect(() => {
    setNodes(buildFlowNodes(topology.nodes));
    setEdges(buildFlowEdges(topology.edges));
  }, [topology]);

  useEffect(() => {
    const latest = recentEvents[0];
    if (!latest || latest.type !== "topology_change") return;
    const nodeId = latest.node_id as string;
    setNodes((nds) =>
      nds.map((n) =>
        n.id === nodeId ? { ...n, data: { ...n.data, flashing: true } } : n
      )
    );
    setTimeout(() => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, flashing: false } } : n
        )
      );
    }, 3000);
  }, [recentEvents]);

  return (
    <div className="w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        onNodeClick={(_evt, rfNode) => {
          const topoNode = topology.nodes.find((n) => n.id === rfNode.id);
          onNodeSelect?.(topoNode ?? null);
        }}
        onPaneClick={() => onNodeSelect?.(null)}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1}
          color="#e2e8f0"
        />
        <Controls />
        <MiniMap
          nodeColor={(n) => {
            const s = (n.data as NodeData)?.node?.status ?? "unknown";
            return s === "healthy" ? "#16a34a"
                 : s === "degraded" ? "#d97706"
                 : s === "down" ? "#dc2626"
                 : "#94a3b8";
          }}
          maskColor="rgba(241,245,249,0.7)"
        />
      </ReactFlow>
    </div>
  );
}
