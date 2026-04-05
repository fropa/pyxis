import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { format } from "date-fns";
import { Activity, AlertTriangle, CheckCircle, Clock, Zap } from "lucide-react";
import { api, ServiceSummary, TimeseriesPoint } from "../api/client";
import clsx from "clsx";

// ── Service card ───────────────────────────────────────────────────────────────

function ServiceCard({
  svc,
  selected,
  onClick,
}: {
  svc: ServiceSummary;
  selected: boolean;
  onClick: () => void;
}) {
  const healthy = svc.error_rate < 0.05;
  const degraded = svc.error_rate >= 0.05 && svc.error_rate < 0.2;

  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full text-left p-4 rounded-xl border transition-all",
        selected
          ? "bg-accent-muted border-accent/40 shadow-glow"
          : "bg-surface border-border hover:border-border-strong hover:bg-raised"
      )}
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <div className={clsx(
            "w-2 h-2 rounded-full flex-shrink-0",
            healthy ? "bg-success" : degraded ? "bg-warning" : "bg-danger"
          )} />
          <span className="text-[13px] font-semibold text-text-1 truncate">{svc.service}</span>
        </div>
        <span className={clsx(
          "text-[11px] font-semibold px-1.5 py-0.5 rounded-md flex-shrink-0",
          healthy ? "bg-success-bg text-success-text" :
          degraded ? "bg-warning-bg text-warning-text" : "bg-danger-bg text-danger-text"
        )}>
          {(svc.error_rate * 100).toFixed(1)}% err
        </span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        {[
          { label: "p99", value: `${svc.p99_ms.toFixed(0)}ms` },
          { label: "p50", value: `${svc.p50_ms.toFixed(0)}ms` },
          { label: "req/hr", value: svc.request_count.toLocaleString() },
        ].map(({ label, value }) => (
          <div key={label}>
            <p className="text-[10px] text-text-3 uppercase tracking-wider">{label}</p>
            <p className="text-[13px] font-bold text-text-1 tabular-nums">{value}</p>
          </div>
        ))}
      </div>
    </button>
  );
}

// ── Latency chart ──────────────────────────────────────────────────────────────

function LatencyChart({ service, hours }: { service: string; hours: number }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ["timeseries", service, hours],
    queryFn: () => api.traces.timeseries(service, hours),
    refetchInterval: 30_000,
  });

  const formatted = data.map((d: TimeseriesPoint) => ({
    ...d,
    time: format(new Date(d.bucket), "HH:mm"),
    error_pct: +(d.error_count / Math.max(d.request_count, 1) * 100).toFixed(1),
  }));

  if (isLoading) {
    return <div className="h-48 flex items-center justify-center text-[12px] text-text-3">Loading…</div>;
  }

  if (formatted.length === 0) {
    return (
      <div className="h-48 flex flex-col items-center justify-center text-center gap-2">
        <Clock size={24} className="text-text-4" />
        <p className="text-[13px] text-text-3">No trace data yet</p>
        <p className="text-[11px] text-text-4">Send spans to <code className="bg-raised px-1 rounded">/api/v1/traces/</code></p>
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={formatted} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgb(var(--c-border))" />
        <XAxis dataKey="time" tick={{ fontSize: 10, fill: "rgb(var(--c-text-3))" }} />
        <YAxis tick={{ fontSize: 10, fill: "rgb(var(--c-text-3))" }} unit="ms" />
        <Tooltip
          contentStyle={{
            background: "rgb(var(--c-surface))",
            border: "1px solid rgb(var(--c-border))",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: "rgb(var(--c-text-1))", fontWeight: 600 }}
        />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="p99_ms" name="p99" stroke="#dc2626" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="p50_ms" name="p50" stroke="#4f46e5" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
        <Line type="monotone" dataKey="avg_ms" name="avg" stroke="#16a34a" strokeWidth={1.5} dot={false} strokeDasharray="2 3" />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Recent traces table ────────────────────────────────────────────────────────

function RecentTraces({ service, hours }: { service: string | null; hours: number }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ["recent-traces", service, hours],
    queryFn: () => api.traces.recent({ hours, service: service ?? undefined, limit: 50 }),
    refetchInterval: 10_000,
  });

  if (isLoading) return <div className="p-4 text-[12px] text-text-3">Loading…</div>;
  if (data.length === 0) return <div className="p-6 text-center text-[12px] text-text-3">No traces in this window</div>;

  return (
    <table className="w-full">
      <thead>
        <tr className="border-b border-border/60">
          {["Service", "Operation", "Duration", "Status", "Spans", "When"].map((h) => (
            <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-text-3">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {data.map((t) => (
          <tr key={t.trace_id} className="border-b border-border/50 last:border-0 hover:bg-raised transition-colors">
            <td className="px-4 py-2.5 text-[12px] font-medium text-text-1">{t.service}</td>
            <td className="px-4 py-2.5 text-[12px] text-text-2 font-mono max-w-[200px] truncate">{t.operation}</td>
            <td className="px-4 py-2.5">
              <span className={clsx(
                "text-[12px] font-bold tabular-nums",
                t.duration_ms > 1000 ? "text-danger-text" : t.duration_ms > 300 ? "text-warning-text" : "text-success-text"
              )}>
                {t.duration_ms >= 1000 ? `${(t.duration_ms / 1000).toFixed(2)}s` : `${t.duration_ms.toFixed(0)}ms`}
              </span>
            </td>
            <td className="px-4 py-2.5">
              {t.status === "error" || (t.status_code && t.status_code >= 500) ? (
                <span className="flex items-center gap-1 text-[11px] font-semibold text-danger-text">
                  <AlertTriangle size={11} /> {t.status_code ?? "error"}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-[11px] font-semibold text-success-text">
                  <CheckCircle size={11} /> {t.status_code ?? "ok"}
                </span>
              )}
            </td>
            <td className="px-4 py-2.5 text-[12px] text-text-3 tabular-nums">{t.span_count}</td>
            <td className="px-4 py-2.5 text-[12px] text-text-3 whitespace-nowrap">
              {format(new Date(t.started_at), "HH:mm:ss")}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

const HOUR_OPTIONS = [
  { label: "Last 15 min", value: 0.25 },
  { label: "Last 1h",     value: 1 },
  { label: "Last 6h",     value: 6 },
  { label: "Last 24h",    value: 24 },
];

export default function TracesView() {
  const [hours, setHours] = useState(1);
  const [selectedService, setSelectedService] = useState<string | null>(null);

  const { data: services = [], isLoading } = useQuery({
    queryKey: ["services", hours],
    queryFn: () => api.traces.services(hours),
    refetchInterval: 15_000,
  });

  const active = selectedService ?? services[0]?.service ?? null;

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 space-y-6">

        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center">
              <Activity size={16} className="text-accent-text" />
            </div>
            <div>
              <h1 className="text-xl font-semibold text-text-1">APM / Traces</h1>
              <p className="text-[13px] text-text-3 mt-0.5">Latency, error rates, and distributed traces</p>
            </div>
          </div>

          {/* Time window picker */}
          <div className="flex items-center gap-1 bg-surface border border-border rounded-lg p-1">
            {HOUR_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setHours(opt.value)}
                className={clsx(
                  "px-3 py-1.5 rounded-md text-[12px] font-medium transition-all",
                  hours === opt.value
                    ? "bg-accent text-white shadow-sm"
                    : "text-text-3 hover:text-text-1 hover:bg-raised"
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Empty state */}
        {!isLoading && services.length === 0 && (
          <div className="bg-surface border border-border rounded-xl p-10 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-accent-muted border border-accent/15 flex items-center justify-center mx-auto">
              <Zap size={20} className="text-accent-text" />
            </div>
            <p className="text-[14px] font-semibold text-text-1">No traces yet</p>
            <p className="text-[13px] text-text-3 max-w-md mx-auto">
              Send spans from your services to start seeing latency data.
            </p>
            <div className="bg-raised border border-border rounded-lg p-4 text-left max-w-lg mx-auto">
              <p className="text-[11px] font-semibold text-text-3 uppercase tracking-wider mb-2">Example — send a span</p>
              <pre className="text-[11px] text-text-2 font-mono overflow-x-auto whitespace-pre">{`curl -X POST http://localhost:8000/api/v1/traces/ \\
  -H "X-API-Key: YOUR_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "spans": [{
      "trace_id": "abc123",
      "span_id":  "def456",
      "service":  "api-gateway",
      "operation":"GET /api/users",
      "duration_ms": 245,
      "status": "ok",
      "status_code": 200
    }]
  }'`}</pre>
            </div>
          </div>
        )}

        {services.length > 0 && (
          <div className="grid grid-cols-[280px_1fr] gap-6">

            {/* Service list */}
            <div className="space-y-2">
              <p className="text-[11px] font-semibold uppercase tracking-wider text-text-3 px-1">
                Services ({services.length})
              </p>
              {services.map((svc) => (
                <ServiceCard
                  key={svc.service}
                  svc={svc}
                  selected={active === svc.service}
                  onClick={() => setSelectedService(svc.service)}
                />
              ))}
            </div>

            {/* Right panel */}
            {active && (
              <div className="space-y-5 min-w-0">

                {/* Latency chart */}
                <div className="bg-surface border border-border rounded-xl shadow-card p-5">
                  <h2 className="text-[13px] font-semibold text-text-1 mb-1">{active} — Latency over time</h2>
                  <p className="text-[11px] text-text-3 mb-4">p99 / p50 / avg response time (ms)</p>
                  <LatencyChart service={active} hours={hours} />
                </div>

                {/* Recent traces */}
                <div className="bg-surface border border-border rounded-xl shadow-card overflow-hidden">
                  <div className="px-5 py-4 border-b border-border">
                    <h2 className="text-[13px] font-semibold text-text-1">Recent Traces — {active}</h2>
                  </div>
                  <RecentTraces service={active} hours={hours} />
                </div>

              </div>
            )}
          </div>
        )}

      </div>
    </div>
  );
}
