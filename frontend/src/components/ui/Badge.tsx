import clsx from "clsx";

type Variant = "success" | "warning" | "danger" | "critical" | "accent" | "neutral" | "info";

const VARIANTS: Record<Variant, string> = {
  success:  "bg-success-bg  text-success-text  border border-success-border",
  warning:  "bg-warning-bg  text-warning-text  border border-warning-border",
  danger:   "bg-danger-bg   text-danger-text   border border-danger-border",
  critical: "bg-critical-bg text-critical-text border border-critical-border",
  accent:   "bg-accent-muted text-accent-text  border border-accent/15",
  info:     "bg-blue-50      text-blue-700     border border-blue-200/60",
  neutral:  "bg-raised      text-text-3        border border-border",
};

const SEVERITY_VARIANT: Record<string, Variant> = {
  critical: "critical",
  high:     "danger",
  medium:   "warning",
  low:      "neutral",
};

const STATUS_VARIANT: Record<string, Variant> = {
  open:           "danger",
  investigating:  "warning",
  resolved:       "success",
  false_positive: "neutral",
};

interface BadgeProps {
  children: React.ReactNode;
  variant?: Variant;
  severity?: string;
  status?: string;
  size?: "xs" | "sm";
  className?: string;
}

export function Badge({
  children,
  variant,
  severity,
  status,
  size = "sm",
  className,
}: BadgeProps) {
  const v =
    variant ??
    (severity ? SEVERITY_VARIANT[severity] : undefined) ??
    (status ? STATUS_VARIANT[status] : undefined) ??
    "neutral";

  return (
    <span
      className={clsx(
        "inline-flex items-center font-medium rounded-md tracking-wide uppercase",
        size === "xs" ? "text-[9px] px-1.5 py-0.5" : "text-[10px] px-2 py-0.5",
        VARIANTS[v],
        className
      )}
    >
      {children}
    </span>
  );
}
