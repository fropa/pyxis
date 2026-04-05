import clsx from "clsx";

type Status =
  | "healthy"
  | "degraded"
  | "down"
  | "unknown"
  | "open"
  | "resolved"
  | "indexing"
  | "pending"
  | "error";

const COLORS: Record<Status, string> = {
  healthy:   "bg-success",
  resolved:  "bg-success",
  degraded:  "bg-warning",
  pending:   "bg-warning",
  indexing:  "bg-accent",
  down:      "bg-danger",
  open:      "bg-danger",
  error:     "bg-danger",
  unknown:   "bg-text-4",
};

const PULSE: Record<Status, boolean> = {
  healthy:  false,
  resolved: false,
  degraded: true,
  pending:  true,
  indexing: true,
  down:     true,
  open:     true,
  error:    false,
  unknown:  false,
};

const RING_COLORS: Record<Status, string> = {
  healthy:   "bg-success/20",
  resolved:  "bg-success/20",
  degraded:  "bg-warning/20",
  pending:   "bg-warning/20",
  indexing:  "bg-accent/20",
  down:      "bg-danger/20",
  open:      "bg-danger/20",
  error:     "bg-danger/20",
  unknown:   "bg-text-4/20",
};

interface StatusDotProps {
  status: Status | string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

export function StatusDot({ status, size = "sm", className }: StatusDotProps) {
  const s = status as Status;
  const color = COLORS[s] ?? "bg-text-4";
  const ring = RING_COLORS[s] ?? "bg-text-4/20";
  const pulse = PULSE[s] ?? false;

  const dotSize =
    size === "sm" ? "w-1.5 h-1.5" :
    size === "md" ? "w-2 h-2" :
    "w-2.5 h-2.5";

  const ringSize =
    size === "sm" ? "w-3 h-3" :
    size === "md" ? "w-4 h-4" :
    "w-5 h-5";

  return (
    <span
      className={clsx(
        "relative inline-flex items-center justify-center flex-shrink-0",
        className
      )}
    >
      {pulse && (
        <span
          className={clsx(
            "absolute rounded-full status-pulse",
            ring,
            ringSize
          )}
        />
      )}
      <span className={clsx("rounded-full relative z-10", color, dotSize)} />
    </span>
  );
}
