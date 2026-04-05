import { useState } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { AlertTriangle, CheckCircle, Clock, Zap, Search, ChevronRight } from "lucide-react";
import { api, getErrorMessage } from "../api/client";
import { useAppStore } from "../store";
import IncidentPanel from "../components/incidents/IncidentPanel";
import { Badge } from "../components/ui/Badge";
import { QueryErrorState } from "../components/ui/QueryErrorState";
import { StatusDot } from "../components/ui/StatusDot";
import { Skeleton } from "../components/ui/Skeleton";
import clsx from "clsx";

const STATUSES = [
  { key: "all",           label: "All",          icon: null },
  { key: "open",          label: "Open",         icon: Zap },
  { key: "investigating", label: "Investigating", icon: Clock },
  { key: "resolved",      label: "Resolved",     icon: CheckCircle },
];

export default function IncidentsView() {
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const setActive = useAppStore((s) => s.setActiveIncidentId);

  const { data: incidents, isLoading, isError, error } = useQuery({
    queryKey: ["incidents", statusFilter],
    queryFn: () =>
      api.incidents.list(
        statusFilter !== "all" ? { status_filter: statusFilter } : undefined
      ),
    refetchInterval: 10_000,
    placeholderData: keepPreviousData,
  });

  // incidents is undefined only on the very first load; after that keepPreviousData holds it
  const list = incidents ?? [];

  const filtered = search
    ? list.filter(
        (i) =>
          i.title.toLowerCase().includes(search.toLowerCase()) ||
          i.rca_summary?.toLowerCase().includes(search.toLowerCase())
      )
    : list;

  const openCount = list.filter((i) => i.status === "open").length;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Header */}
      <div className="flex-shrink-0 bg-surface border-b border-border px-6 py-4">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-xl font-semibold text-text-1">Incidents</h1>
            <p className="text-[13px] text-text-3 mt-0.5">
              {openCount > 0
                ? `${openCount} open incident${openCount > 1 ? "s" : ""} requiring attention`
                : "No open incidents — all clear"}
            </p>
          </div>

          {/* Search */}
          <div className="relative">
            <Search
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-text-4 pointer-events-none"
            />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search incidents…"
              className="pl-8 pr-4 py-2 bg-bg border border-border rounded-lg text-[13px] text-text-1 placeholder:text-text-4 focus:outline-none focus:border-accent/40 focus:ring-2 focus:ring-accent/10 w-60 transition-all"
            />
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1">
          {STATUSES.map(({ key, label, icon: Icon }) => (
            <button
              key={key}
              onClick={() => setStatusFilter(key)}
              className={clsx(
                "flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-[12px] font-medium transition-all duration-150",
                statusFilter === key
                  ? "bg-accent text-white shadow-sm"
                  : "text-text-3 hover:text-text-1 hover:bg-raised border border-transparent"
              )}
            >
              {Icon && <Icon size={11} />}
              {label}
              {key === "open" && openCount > 0 && (
                <span className="min-w-[16px] h-4 px-1 bg-white/25 text-white text-[10px] font-bold rounded-full flex items-center justify-center">
                  {openCount}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-6">
        {isError ? (
          <QueryErrorState message={getErrorMessage(error)} />
        ) : isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="bg-surface border border-border rounded-xl p-4 flex items-center gap-4"
              >
                <Skeleton className="w-2 h-2 rounded-full flex-shrink-0" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-3.5 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
                <Skeleton className="h-5 w-16 rounded-md flex-shrink-0" />
                <Skeleton className="h-5 w-20 rounded-md flex-shrink-0" />
                <Skeleton className="h-3 w-24 flex-shrink-0" />
              </div>
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="w-14 h-14 rounded-full bg-success-bg border border-success-border flex items-center justify-center mb-4">
              <CheckCircle size={24} className="text-success" />
            </div>
            <p className="text-[15px] font-semibold text-text-1">No incidents found</p>
            <p className="text-[13px] text-text-3 mt-1 max-w-xs leading-relaxed">
              {search
                ? "Try adjusting your search or filter"
                : "Your infrastructure is running smoothly"}
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((inc) => (
              <button
                key={inc.id}
                onClick={() => setActive(inc.id)}
                className="w-full text-left group bg-surface border border-border hover:border-accent/30 hover:shadow-md rounded-xl p-4 transition-all duration-150"
              >
                <div className="flex items-center gap-4">
                  <StatusDot status={inc.status} size="md" className="flex-shrink-0" />

                  <div className="flex-1 min-w-0">
                    <p className="text-[13px] font-semibold text-text-1 group-hover:text-accent-text truncate transition-colors">
                      {inc.title}
                    </p>
                    {inc.rca_summary && (
                      <p className="text-[12px] text-text-3 mt-0.5 line-clamp-1">
                        {inc.rca_summary}
                      </p>
                    )}
                  </div>

                  <Badge severity={inc.severity} className="flex-shrink-0">
                    {inc.severity}
                  </Badge>
                  <Badge status={inc.status} className="flex-shrink-0">
                    {inc.status.replace("_", " ")}
                  </Badge>

                  {inc.rca_confidence != null ? (
                    <div className="flex items-center gap-2 flex-shrink-0 w-28">
                      <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
                        <div
                          className={clsx(
                            "h-full rounded-full",
                            inc.rca_confidence > 0.7 ? "bg-success" :
                            inc.rca_confidence > 0.4 ? "bg-warning" : "bg-danger"
                          )}
                          style={{ width: `${Math.round(inc.rca_confidence * 100)}%` }}
                        />
                      </div>
                      <span className="text-[11px] text-text-3 tabular-nums w-7 text-right">
                        {Math.round(inc.rca_confidence * 100)}%
                      </span>
                    </div>
                  ) : (
                    <span className="text-[12px] text-text-4 flex-shrink-0 w-28 text-right">—</span>
                  )}

                  <span className="text-[12px] text-text-3 flex-shrink-0 w-28 text-right whitespace-nowrap">
                    {formatDistanceToNow(new Date(inc.started_at), { addSuffix: true })}
                  </span>

                  <ChevronRight
                    size={14}
                    className="text-text-4 flex-shrink-0 group-hover:text-accent transition-colors"
                  />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      <IncidentPanel />
    </div>
  );
}
