import { NavLink } from "react-router-dom";
import {
  LayoutDashboard, Network, AlertTriangle,
  Download, Settings, Activity, Sun, Moon, FlaskConical, Shield,
} from "lucide-react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useAppStore } from "../store";
import { useTheme } from "../hooks/useTheme";
import { StatusDot } from "./ui/StatusDot";
import clsx from "clsx";

const NAV_MAIN = [
  { to: "/dashboard",  label: "Dashboard",     icon: LayoutDashboard },
  { to: "/topology",   label: "Topology",      icon: Network },
  { to: "/incidents",  label: "Incidents",     icon: AlertTriangle },
  { to: "/traces",     label: "APM / Traces",  icon: Activity },
];

const NAV_SETUP = [
  { to: "/install",     label: "Install Agent", icon: Download },
  { to: "/onboarding",  label: "Settings",      icon: Settings },
  { to: "/playground",  label: "Playground",    icon: FlaskConical },
  { to: "/admin",       label: "Admin",         icon: Shield },
];

function NavItem({
  to,
  label,
  icon: Icon,
  badge,
}: {
  to: string;
  label: string;
  icon: React.ElementType;
  badge?: number;
}) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        clsx(
          "group flex items-center gap-2.5 px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150",
          isActive
            ? "bg-side-active text-side-accent"
            : "text-side-muted hover:text-side-text hover:bg-side-hover"
        )
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            size={15}
            className={clsx(
              "flex-shrink-0 transition-colors",
              isActive
                ? "text-side-accent"
                : "text-side-muted group-hover:text-side-text"
            )}
          />
          <span className="flex-1">{label}</span>
          {badge != null && badge > 0 && (
            <span className="min-w-[18px] h-[18px] px-1 bg-danger text-white text-[10px] font-bold rounded-full flex items-center justify-center">
              {badge > 99 ? "99+" : badge}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}

function NavSection({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-0.5">
      <p className="px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-widest text-side-muted/60">
        {label}
      </p>
      {children}
    </div>
  );
}

export default function Layout({ children }: { children: React.ReactNode }) {
  useWebSocket();
  const recentEvents = useAppStore((s) => s.recentEvents);
  const apiKey = useAppStore((s) => s.apiKey);
  const openIncidents = recentEvents.filter((e) => e.type === "incident_opened").length;
  const { theme, toggleTheme } = useTheme();

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ── Sidebar (always dark) ── */}
      <aside className="w-[220px] flex-shrink-0 flex flex-col bg-side-bg sidebar-scroll">

        {/* Logo */}
        <div className="flex items-center gap-3 px-4 h-14 border-b border-side-border flex-shrink-0">
          <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center flex-shrink-0">
            <Activity size={14} className="text-white" />
          </div>
          <span className="font-bold text-side-text text-[15px] tracking-tight">
            Pyxis
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto p-2 min-h-0 sidebar-scroll">
          <NavSection label="Monitor">
            {NAV_MAIN.map((item) => (
              <NavItem
                key={item.to}
                {...item}
                badge={item.to === "/incidents" ? openIncidents : undefined}
              />
            ))}
          </NavSection>
          <NavSection label="Setup">
            {NAV_SETUP.map((item) => (
              <NavItem key={item.to} {...item} />
            ))}
          </NavSection>
        </nav>

        {/* Bottom: theme toggle + connection status */}
        <div className="flex-shrink-0 p-3 border-t border-side-border space-y-2">

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-side-muted hover:text-side-text hover:bg-side-hover transition-all duration-150"
          >
            {theme === "light" ? (
              <>
                <Moon size={14} className="flex-shrink-0" />
                <span className="text-[12px] font-medium">Dark mode</span>
              </>
            ) : (
              <>
                <Sun size={14} className="flex-shrink-0" />
                <span className="text-[12px] font-medium">Light mode</span>
              </>
            )}
          </button>

          {/* Connection status */}
          <div className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-side-hover">
            <StatusDot status={apiKey ? "healthy" : "unknown"} size="sm" />
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-medium text-side-text leading-none mb-0.5">
                {apiKey ? "Connected" : "Not configured"}
              </p>
              <p className="text-[11px] text-side-muted truncate">
                {apiKey ? `Key ···${apiKey.slice(-6)}` : "Add key in Settings"}
              </p>
            </div>
          </div>
        </div>
      </aside>

      {/* ── Main content (theme-switchable) ── */}
      <main className="flex-1 min-w-0 overflow-auto bg-bg">
        {children}
      </main>
    </div>
  );
}
