import { formatDistanceToNow } from "date-fns";
import { AlertTriangle, Info, Zap, GitBranch, CheckCircle, Bell } from "lucide-react";
import { useAppStore, WsEvent } from "../../store";
import clsx from "clsx";

const EVENT_CONFIG: Record<
  string,
  {
    icon: React.ElementType;
    label: string;
    stripe: string;
    iconBg: string;
    iconColor: string;
  }
> = {
  anomaly_detected: {
    icon: AlertTriangle,
    label: "Anomaly",
    stripe: "border-l-warning",
    iconBg: "bg-warning-bg",
    iconColor: "text-warning",
  },
  incident_opened: {
    icon: Zap,
    label: "Incident",
    stripe: "border-l-danger",
    iconBg: "bg-danger-bg",
    iconColor: "text-danger",
  },
  rca_ready: {
    icon: Info,
    label: "RCA Ready",
    stripe: "border-l-accent",
    iconBg: "bg-accent-muted",
    iconColor: "text-accent-text",
  },
  topology_change: {
    icon: GitBranch,
    label: "Topology",
    stripe: "border-l-success",
    iconBg: "bg-success-bg",
    iconColor: "text-success-text",
  },
  incident_resolved: {
    icon: CheckCircle,
    label: "Resolved",
    stripe: "border-l-success",
    iconBg: "bg-success-bg",
    iconColor: "text-success-text",
  },
};

function EventRow({
  event,
  isNew,
}: {
  event: WsEvent & { _ts?: number };
  isNew: boolean;
}) {
  const cfg = EVENT_CONFIG[event.type] ?? {
    icon: Info,
    label: event.type,
    stripe: "border-l-border",
    iconBg: "bg-raised",
    iconColor: "text-text-3",
  };
  const Icon = cfg.icon;
  const setActive = useAppStore((s) => s.setActiveIncidentId);

  const message =
    (event.message as string) ||
    (event.title as string) ||
    (event.rca_summary as string) ||
    (event.node_name as string) ||
    "";

  return (
    <button
      onClick={() => {
        if (event.incident_id) setActive(event.incident_id as string);
      }}
      className={clsx(
        "w-full text-left flex gap-3 px-4 py-3 border-l-[3px] border-b border-border/50",
        "hover:bg-raised transition-colors duration-100",
        cfg.stripe,
        isNew && "animate-slide-up"
      )}
    >
      <div
        className={clsx(
          "w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5",
          cfg.iconBg
        )}
      >
        <Icon size={12} className={cfg.iconColor} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2 mb-0.5">
          <span className={clsx("text-[11px] font-semibold", cfg.iconColor)}>
            {cfg.label}
          </span>
          <span className="text-[10px] text-text-4 flex-shrink-0">
            {event._ts
              ? formatDistanceToNow(new Date(event._ts), { addSuffix: true })
              : "just now"}
          </span>
        </div>
        <p className="text-[12px] text-text-2 truncate leading-snug">{message}</p>
      </div>
    </button>
  );
}

export default function AlertFeed() {
  const events = useAppStore((s) => s.recentEvents);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3.5 border-b border-border flex-shrink-0">
        <div className="flex items-center gap-2">
          <Bell size={13} className="text-text-3" />
          <h2 className="text-[13px] font-semibold text-text-1">Live Events</h2>
        </div>
        {events.length > 0 && (
          <span className="text-[11px] text-text-3">{events.length}</span>
        )}
      </div>

      {/* Events */}
      <div className="flex-1 overflow-y-auto">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full py-12 text-center px-6">
            <div className="w-10 h-10 rounded-full bg-raised border border-border flex items-center justify-center mb-3">
              <ActivityIcon size={18} className="text-text-4" />
            </div>
            <p className="text-[13px] font-medium text-text-2">Waiting for events</p>
            <p className="text-[12px] text-text-3 mt-1 leading-relaxed">
              Events appear here as your agents send logs
            </p>
          </div>
        ) : (
          events.map((e, i) => (
            <EventRow key={i} event={e} isNew={i === 0} />
          ))
        )}
      </div>
    </div>
  );
}

function ActivityIcon({
  size,
  className,
}: {
  size: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  );
}
