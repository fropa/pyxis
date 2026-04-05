import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, HeatmapEntry } from "../../api/client";
import clsx from "clsx";

const DAYS = 90;
const COLS = 13; // weeks

function getCellColor(count: number): string {
  if (count === 0) return "bg-raised border border-border";
  if (count === 1) return "bg-warning/30 border border-warning/40";
  if (count === 2) return "bg-warning/60 border border-warning/60";
  if (count <= 4) return "bg-danger/50 border border-danger/50";
  return "bg-danger border border-danger/80";
}

function buildGrid(data: HeatmapEntry[]): { date: string; count: number }[][] {
  const map = new Map(data.map((d) => [d.date, d.count]));
  const today = new Date();
  const grid: { date: string; count: number }[][] = [];

  // Start from (DAYS) ago, aligned to Sunday
  const start = new Date(today);
  start.setDate(start.getDate() - DAYS);
  start.setDate(start.getDate() - start.getDay()); // back to Sunday

  let cursor = new Date(start);
  for (let col = 0; col < COLS; col++) {
    const week: { date: string; count: number }[] = [];
    for (let row = 0; row < 7; row++) {
      const dateStr = cursor.toISOString().slice(0, 10);
      week.push({ date: dateStr, count: map.get(dateStr) ?? 0 });
      cursor.setDate(cursor.getDate() + 1);
    }
    grid.push(week);
  }
  return grid;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function IncidentHeatmap() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["heatmap"],
    queryFn: () => api.heatmap.get(DAYS),
    refetchInterval: 60_000,
  });

  const grid = useMemo(() => buildGrid(data), [data]);
  const total = data.reduce((s, d) => s + d.count, 0);

  const monthLabels = useMemo(() => {
    const labels: { label: string; col: number }[] = [];
    let lastMonth = -1;
    grid.forEach((week, col) => {
      const d = new Date(week[0].date);
      if (d.getMonth() !== lastMonth) {
        labels.push({ label: d.toLocaleString("default", { month: "short" }), col });
        lastMonth = d.getMonth();
      }
    });
    return labels;
  }, [grid]);

  return (
    <div className="bg-surface border border-border rounded-xl shadow-card p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-[13px] font-semibold text-text-1">Incident Activity</h2>
          <p className="text-[11px] text-text-3 mt-0.5">Last 90 days · {total} total incidents</p>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-text-3">
          <span>Less</span>
          {[0, 1, 2, 3, 4].map((n) => (
            <div key={n} className={clsx("w-3 h-3 rounded-sm", getCellColor(n))} />
          ))}
          <span>More</span>
        </div>
      </div>

      {isLoading ? (
        <div className="h-24 flex items-center justify-center text-[12px] text-text-3">Loading…</div>
      ) : (
        <div className="overflow-x-auto">
          {/* Month labels */}
          <div className="flex gap-1 mb-1 pl-8">
            {grid.map((_, col) => {
              const label = monthLabels.find((m) => m.col === col);
              return (
                <div key={col} className="w-3 text-[9px] text-text-3 flex-shrink-0">
                  {label?.label ?? ""}
                </div>
              );
            })}
          </div>

          <div className="flex gap-1">
            {/* Weekday labels */}
            <div className="flex flex-col gap-1 mr-1">
              {WEEKDAYS.map((d, i) => (
                <div key={d} className="h-3 text-[9px] text-text-3 leading-3 w-6 text-right pr-1">
                  {i % 2 === 1 ? d.slice(0, 1) : ""}
                </div>
              ))}
            </div>

            {/* Grid cells */}
            {grid.map((week, col) => (
              <div key={col} className="flex flex-col gap-1">
                {week.map(({ date, count }) => (
                  <div
                    key={date}
                    title={`${date}: ${count} incident${count !== 1 ? "s" : ""}`}
                    className={clsx("w-3 h-3 rounded-sm cursor-default transition-opacity hover:opacity-75", getCellColor(count))}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
