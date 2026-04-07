import { useState } from "react";
import { Check, Copy, Server, Box, Cloud, Terminal, Download } from "lucide-react";
import { useAppStore } from "../store";
import clsx from "clsx";

// Derive API URL from the current page — works for any host, IP, or domain
// Users behind nginx on port 80 get "http://x.x.x.x", dev gets "http://localhost:5173"
function getDefaultApiUrl(): string {
  const { protocol, hostname, port } = window.location;
  // If running on the Vite dev port directly, point to the backend port
  if (port === "5173") return `${protocol}//${hostname}:8000`;
  // Otherwise (nginx / production) use the same origin
  return `${protocol}//${hostname}${port ? `:${port}` : ""}`;
}

type PlatformId = "linux" | "kubernetes" | "docker" | "macos";

const PLATFORMS: Array<{
  id: PlatformId;
  label: string;
  description: string;
  icon: React.ElementType;
  badge?: string;
}> = [
  {
    id: "linux",
    label: "Linux Server",
    description: "Ubuntu, Debian, RHEL, Amazon Linux — any systemd distro",
    icon: Server,
  },
  {
    id: "kubernetes",
    label: "Kubernetes",
    description: "DaemonSet on every node, watches cluster events",
    icon: Box,
    badge: "Recommended",
  },
  {
    id: "docker",
    label: "Docker",
    description: "Single container for hosts running Docker without K8s",
    icon: Cloud,
  },
  {
    id: "macos",
    label: "macOS (dev)",
    description: "Local development — tails system logs",
    icon: Terminal,
  },
];

const INSTALLS_WHAT: Record<PlatformId, string[]> = {
  linux: [
    "Downloads shipper.py to /opt/pyxis/",
    "Creates systemd service (auto-starts on reboot)",
    "Tails /var/log/syslog, /var/log/messages, /var/log/auth.log",
    "Sends heartbeat every 60 s (silent-death detection)",
    "Buffers events to disk if backend unreachable",
  ],
  kubernetes: [
    "DaemonSet on every node including control-plane",
    "Watches all K8s events cluster-wide",
    "Tails node /var/log/ for syslog",
    "RBAC: read-only access to events, nodes, pods, deployments",
    "Per-pod disk buffer — survives network blips",
  ],
  docker: [
    "Single container with auto-restart policy",
    "Tails /var/log/ from the host (read-only mount)",
    "Disk buffer volume for offline resilience",
  ],
  macos: [
    "Runs shipper.py directly (no systemd)",
    "Tails /var/log/system.log",
    "Useful for local development and testing",
  ],
};

// ── Sub-components ─────────────────────────────────────────────────────────────

function copyToClipboard(text: string): Promise<void> {
  // navigator.clipboard requires HTTPS — fall back to execCommand for HTTP
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  return new Promise((resolve) => {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;left:-9999px;top:-9999px";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    resolve();
  });
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        copyToClipboard(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        });
      }}
      className={clsx(
        "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[11px] font-semibold transition-all",
        copied
          ? "bg-success-bg text-success-text border border-success-border"
          : "bg-white/10 hover:bg-white/20 text-slate-300 border border-white/10"
      )}
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
      {copied ? "Copied!" : "Copy"}
    </button>
  );
}

function CommandBlock({ children }: { children: string }) {
  return (
    <div className="relative group rounded-xl overflow-hidden border border-slate-700 shadow-card">
      <div className="flex items-center justify-between px-4 py-2.5 bg-slate-800 border-b border-slate-700">
        <div className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-slate-600" />
          <span className="w-2.5 h-2.5 rounded-full bg-slate-600" />
          <span className="w-2.5 h-2.5 rounded-full bg-slate-600" />
        </div>
        <CopyButton text={children} />
      </div>
      <pre className="bg-slate-900 px-4 py-4 text-[12px] text-emerald-400 font-mono overflow-x-auto whitespace-pre-wrap break-all leading-relaxed">
        {children}
      </pre>
    </div>
  );
}

function PlatformCard({
  platform,
  selected,
  onSelect,
}: {
  platform: (typeof PLATFORMS)[0];
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = platform.icon;
  return (
    <button
      onClick={onSelect}
      className={clsx(
        "relative text-left p-4 rounded-xl border-2 transition-all duration-150",
        selected
          ? "border-accent bg-accent-muted shadow-glow"
          : "border-border bg-surface hover:border-border-strong hover:shadow-card"
      )}
    >
      {platform.badge && (
        <span className="absolute top-3 right-3 text-[10px] bg-accent text-white px-2 py-0.5 rounded-full font-semibold">
          {platform.badge}
        </span>
      )}
      <div
        className={clsx(
          "w-9 h-9 rounded-xl flex items-center justify-center mb-3",
          selected ? "bg-accent text-white" : "bg-raised text-text-3"
        )}
      >
        <Icon size={18} />
      </div>
      <p className="text-[13px] font-semibold text-text-1">{platform.label}</p>
      <p className="text-[12px] text-text-3 mt-1 leading-relaxed">
        {platform.description}
      </p>
    </button>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────────

export default function InstallAgent() {
  const apiKey = useAppStore((s) => s.apiKey);
  const [selected, setSelected] = useState<PlatformId>("kubernetes");
  const [k8sNamespace, setK8sNamespace] = useState("monitoring");
  const [k8sSources, setK8sSources] = useState("k8s,syslog");
  const [linuxSources, setLinuxSources] = useState("syslog");
  const [customApiUrl, setCustomApiUrl] = useState("");

  const effectiveApiUrl = customApiUrl.trim() || getDefaultApiUrl();

  const commands = buildCommands({
    apiKey,
    apiUrl: effectiveApiUrl,
    selected,
    k8sNamespace,
    k8sSources,
    linuxSources,
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center flex-shrink-0">
            <Download size={18} className="text-accent-text" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-text-1">Install Agent</h1>
            <p className="text-[13px] text-text-3 mt-0.5">
              Pick a platform — get one command — logs start flowing in seconds.
            </p>
          </div>
        </div>

        {/* API key warning */}
        {!apiKey && (
          <div className="flex items-start gap-3 bg-warning-bg border border-warning-border rounded-xl p-4">
            <span className="w-2 h-2 rounded-full bg-warning mt-1.5 flex-shrink-0" />
            <p className="text-[13px] text-warning-text">
              No API key set.{" "}
              <a href="/onboarding" className="font-semibold underline">
                Go to Settings
              </a>{" "}
              first — your key will be embedded in the install command automatically.
            </p>
          </div>
        )}

        {/* API URL — auto-detected, override for multi-IP setups */}
        <div className="bg-surface border border-border rounded-xl p-4 space-y-1.5">
          <label className="block text-[12px] font-semibold text-text-2">
            Pyxis API URL
            <span className="ml-2 text-[11px] font-normal text-text-4">
              (auto-detected — override if agents reach this server on a different IP)
            </span>
          </label>
          <input
            value={customApiUrl}
            onChange={(e) => setCustomApiUrl(e.target.value)}
            placeholder={getDefaultApiUrl()}
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text-1 placeholder:text-text-4 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 font-mono"
          />
        </div>

        {/* Platform picker */}
        <div className="grid grid-cols-2 gap-3">
          {PLATFORMS.map((p) => (
            <PlatformCard
              key={p.id}
              platform={p}
              selected={selected === p.id}
              onSelect={() => setSelected(p.id)}
            />
          ))}
        </div>

        {/* Options */}
        {selected === "kubernetes" && (
          <div className="flex gap-4">
            <div className="flex-1">
              <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
                Namespace
              </label>
              <input
                value={k8sNamespace}
                onChange={(e) => setK8sNamespace(e.target.value)}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-[13px] text-text-1 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10"
              />
            </div>
            <div className="flex-1">
              <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
                Sources
              </label>
              <input
                value={k8sSources}
                onChange={(e) => setK8sSources(e.target.value)}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-[13px] text-text-1 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10"
              />
            </div>
          </div>
        )}
        {selected === "linux" && (
          <div className="max-w-xs">
            <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
              Sources
            </label>
            <input
              value={linuxSources}
              onChange={(e) => setLinuxSources(e.target.value)}
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-[13px] text-text-1 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10"
            />
          </div>
        )}

        {/* Commands */}
        <div className="space-y-5">
          {commands.map((cmd, i) => (
            <div key={i}>
              <div className="flex items-center gap-2.5 mb-2">
                <span className="w-5 h-5 rounded-full bg-accent text-white text-[10px] font-bold flex items-center justify-center flex-shrink-0">
                  {i + 1}
                </span>
                <span className="text-[13px] font-semibold text-text-1">{cmd.label}</span>
              </div>
              <CommandBlock>{cmd.command}</CommandBlock>
              {cmd.note && (
                <p className="text-[12px] text-text-3 mt-1.5 ml-7 leading-relaxed">
                  {cmd.note}
                </p>
              )}
            </div>
          ))}
        </div>

        {/* What gets installed */}
        <div className="bg-surface border border-border rounded-xl shadow-card p-5">
          <h3 className="text-[13px] font-semibold text-text-1 mb-3 flex items-center gap-2">
            <Check size={14} className="text-success" />
            What gets installed
          </h3>
          <ul className="space-y-2">
            {INSTALLS_WHAT[selected].map((item, i) => (
              <li key={i} className="flex items-start gap-2.5 text-[13px] text-text-2">
                <Check size={13} className="text-success mt-0.5 flex-shrink-0" />
                {item}
              </li>
            ))}
          </ul>
        </div>

        {/* Verify */}
        <div className="bg-surface border border-border rounded-xl shadow-card p-5">
          <h3 className="text-[13px] font-semibold text-text-1 mb-2">
            Verify it's working
          </h3>
          <p className="text-[12px] text-text-3 mb-3 leading-relaxed">
            After installing, your node should appear in{" "}
            <a href="/topology" className="text-accent-text font-medium hover:underline">
              Topology
            </a>{" "}
            within 30 seconds. To test manually:
          </p>
          <CommandBlock>
            {`curl -s -X POST ${effectiveApiUrl}/api/v1/ingest/ \\
  -H "X-API-Key: ${apiKey || "YOUR_API_KEY"}" \\
  -H "Content-Type: application/json" \\
  -d '{"events":[{"source":"syslog","level":"error","node_name":"test-host","raw":"test: disk full","parsed":{}}]}'`}
          </CommandBlock>
          <p className="text-[12px] text-text-3 mt-2 leading-relaxed">
            This should open an incident in{" "}
            <a href="/incidents" className="text-accent-text font-medium hover:underline">
              Incidents
            </a>{" "}
            within ~15 seconds.
          </p>
        </div>
      </div>
    </div>
  );
}

// ── Command builder ────────────────────────────────────────────────────────────

function buildCommands(opts: {
  apiKey: string;
  apiUrl: string;
  selected: PlatformId;
  k8sNamespace: string;
  k8sSources: string;
  linuxSources: string;
}): Array<{ label: string; command: string; note?: string }> {
  const { apiKey, apiUrl, selected, k8sNamespace, k8sSources, linuxSources } = opts;
  const key = apiKey || "YOUR_API_KEY";
  const url = apiUrl.replace(/\/$/, "");
  const base = `${url}/install`;

  switch (selected) {
    case "linux":
      return [
        {
          label: "Run the installer (requires sudo)",
          command: `curl -fsSL "${base}/linux?api_key=${key}&api_url=${url}&sources=${linuxSources}" | sudo bash`,
          note: "Downloads shipper.py, creates a systemd service and starts it.",
        },
      ];

    case "kubernetes":
      return [
        {
          label: "Apply the manifest",
          command: `kubectl apply -f "${base}/k8s-manifest?api_key=${key}&api_url=${url}&namespace=${k8sNamespace}&sources=${encodeURIComponent(k8sSources)}"`,
          note: `Creates namespace '${k8sNamespace}', DaemonSet, RBAC, and Secret.`,
        },
        {
          label: "Or use Helm (recommended for production)",
          command: `helm repo add pyxis https://charts.pyxis.io\nhelm repo update\ncurl -fsSL "${base}/helm-values?api_key=${key}&api_url=${url}&namespace=${k8sNamespace}&sources=${encodeURIComponent(k8sSources)}" -o pyxis-values.yaml\nhelm install pyxis-agent pyxis/pyxis-agent \\\n  -f pyxis-values.yaml \\\n  -n ${k8sNamespace} --create-namespace`,
          note: "Upgrade later with `helm upgrade pyxis-agent …`",
        },
        {
          label: "Verify the DaemonSet",
          command: `kubectl get daemonset pyxis-agent -n ${k8sNamespace}\nkubectl get pods -n ${k8sNamespace} -l app=pyxis-agent`,
        },
      ];

    case "docker":
      return [
        {
          label: "Run the container",
          command: `docker run -d --name pyxis-agent --restart=always \\\n  -e PYXIS_API_KEY=${key} \\\n  -e PYXIS_API_URL=${url} \\\n  -e PYXIS_SOURCES=syslog \\\n  -v /var/log:/var/log:ro \\\n  -v pyxis-buffer:/var/lib/pyxis/buffer \\\n  python:3.12-slim \\\n  bash -c "pip install -q requests && curl -fsSL ${url}/install/shipper.py | python3 - --sources syslog"`,
          note: "Mounts host /var/log read-only. Buffer volume survives restarts.",
        },
        {
          label: "View logs",
          command: "docker logs pyxis-agent -f",
        },
      ];

    case "macos":
      return [
        {
          label: "Download and run the shipper",
          command: `curl -fsSL "${base}/shipper.py" -o ~/pyxis-shipper.py\nexport PYXIS_API_KEY=${key}\nexport PYXIS_API_URL=${url}\npython3 ~/pyxis-shipper.py --sources syslog`,
          note: "For development only — runs in the foreground. Ctrl+C to stop.",
        },
      ];
  }
}
