import { useQuery } from "@tanstack/react-query";
import { Shield, AlertTriangle, CheckCircle, TrendingUp, Users } from "lucide-react";
import { api } from "../api/client";
import clsx from "clsx";

function HealthBar({ score }: { score: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-raised border border-border rounded-full overflow-hidden">
        <div
          className={clsx(
            "h-full rounded-full transition-all",
            score >= 80 ? "bg-success" : score >= 50 ? "bg-warning" : "bg-danger"
          )}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className={clsx(
        "text-[12px] font-bold tabular-nums w-10 text-right",
        score >= 80 ? "text-success-text" : score >= 50 ? "text-warning-text" : "text-danger-text"
      )}>
        {Math.round(score)}%
      </span>
    </div>
  );
}

export default function AdminView() {
  const { data: stats = [], isLoading } = useQuery({
    queryKey: ["tenant-stats"],
    queryFn: api.admin.tenantStats,
    refetchInterval: 30_000,
  });

  const totalOpen     = stats.reduce((s, t) => s + t.open_incidents, 0);
  const totalResolved = stats.reduce((s, t) => s + t.resolved_last_7d, 0);
  const avgHealth     = stats.length ? stats.reduce((s, t) => s + t.health_score, 0) / stats.length : 100;

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 space-y-6 max-w-[1000px]">

        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center">
            <Shield size={16} className="text-accent-text" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-text-1">Admin Dashboard</h1>
            <p className="text-[13px] text-text-3 mt-0.5">All tenants overview</p>
          </div>
        </div>

        {/* Summary cards */}
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: "Tenants", value: stats.length, icon: Users, color: "text-accent", bg: "bg-accent-muted" },
            { label: "Open Incidents", value: totalOpen, icon: AlertTriangle, color: "text-danger", bg: "bg-danger-bg" },
            { label: "Resolved (7d)", value: totalResolved, icon: TrendingUp, color: "text-success", bg: "bg-success-bg" },
          ].map(({ label, value, icon: Icon, color, bg }) => (
            <div key={label} className="bg-surface border border-border rounded-xl p-5 shadow-card">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-text-3 mb-1">{label}</p>
                  <p className="text-3xl font-bold text-text-1 tabular-nums">{value}</p>
                </div>
                <div className={clsx("w-10 h-10 rounded-xl flex items-center justify-center", bg)}>
                  <Icon size={18} className={color} />
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Tenants table */}
        <div className="bg-surface border border-border rounded-xl shadow-card overflow-hidden">
          <div className="px-5 py-4 border-b border-border">
            <h2 className="text-[13px] font-semibold text-text-1">Tenants</h2>
          </div>

          {isLoading ? (
            <div className="p-8 text-center text-[13px] text-text-3">Loading…</div>
          ) : stats.length === 0 ? (
            <div className="p-8 text-center text-[13px] text-text-3">No tenants yet</div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-border/60">
                  {["Tenant", "Plan", "Open", "Resolved 7d", "Total", "Health"].map((h) => (
                    <th key={h} className="px-5 py-3 text-left text-[11px] font-semibold uppercase tracking-wider text-text-3">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stats.map((t) => (
                  <tr key={t.id} className="border-b border-border/70 last:border-0 hover:bg-raised transition-colors">
                    <td className="px-5 py-3">
                      <p className="text-[13px] font-medium text-text-1">{t.name}</p>
                      <p className="text-[11px] text-text-3 font-mono">{t.id.slice(0, 8)}…</p>
                    </td>
                    <td className="px-5 py-3">
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md bg-accent-muted text-accent-text border border-accent/15 capitalize">
                        {t.plan}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <span className={clsx("text-[13px] font-semibold tabular-nums", t.open_incidents > 0 ? "text-danger-text" : "text-success-text")}>
                        {t.open_incidents}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-[13px] text-text-2 tabular-nums">{t.resolved_last_7d}</td>
                    <td className="px-5 py-3 text-[13px] text-text-2 tabular-nums">{t.total_incidents}</td>
                    <td className="px-5 py-3 w-40">
                      <HealthBar score={t.health_score} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

      </div>
    </div>
  );
}
