import { useState } from "react";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import {
  X, FileCode, CheckCircle, Loader2, Link, BookOpen, ChevronDown, ChevronUp,
  Copy, Check, ScrollText, Zap,
} from "lucide-react";
import { api, getErrorMessage } from "../../api/client";
import { useAppStore } from "../../store";
import { Badge } from "../ui/Badge";
import { QueryErrorState } from "../ui/QueryErrorState";
import { SkeletonText } from "../ui/Skeleton";
import clsx from "clsx";

function CopyButton({ text, alwaysVisible = false }: { text: string; alwaysVisible?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 2000);
        });
      }}
      className={`absolute top-2 right-2 p-1.5 rounded-md border transition-all ${
        alwaysVisible
          ? "bg-[#2d2d4e] border-[#3d3d6e] text-[#7c8cf8] hover:text-white"
          : "bg-surface/80 border-border text-text-3 hover:text-text-1 opacity-0 group-hover:opacity-100"
      }`}
      title="Copy"
    >
      {copied ? <Check size={11} /> : <Copy size={11} />}
    </button>
  );
}

/**
 * Extract shell command blocks from RCA markdown.
 * Grabs content inside ``` fences that look like shell commands.
 */
function extractCommands(rca: string): string[] {
  const blocks: string[] = [];
  const fence = /```(?:bash|sh|shell|zsh|console)?\n([\s\S]*?)```/gi;
  let m: RegExpExecArray | null;
  while ((m = fence.exec(rca)) !== null) {
    const block = m[1].trim();
    if (block && block.length < 800) blocks.push(block);
  }
  return blocks.slice(0, 8); // cap at 8 command blocks
}

const mdComponents = {
  code({ className, children, ...props }: React.HTMLAttributes<HTMLElement> & { inline?: boolean }) {
    const isBlock = !props.inline;
    const text = String(children).replace(/\n$/, "");
    if (isBlock) {
      return (
        <div className="relative group my-3">
          <pre className="bg-[#1e1e1e] text-[#d4d4d4] rounded-xl p-4 overflow-x-auto text-[12px] font-mono leading-relaxed">
            <code>{text}</code>
          </pre>
          <CopyButton text={text} />
        </div>
      );
    }
    return <code className={className} {...props}>{children}</code>;
  },
};

const SEVERITY_BAR: Record<string, string> = {
  critical: "bg-critical",
  high:     "bg-danger",
  medium:   "bg-warning",
  low:      "bg-text-3",
};

export default function IncidentPanel() {
  const incidentId = useAppStore((s) => s.activeIncidentId);
  const setActive  = useAppStore((s) => s.setActiveIncidentId);
  const [showRunbook, setShowRunbook] = useState(false);
  const [showPostmortem, setShowPostmortem] = useState(false);
  const qc = useQueryClient();

  const { data: incident, isLoading, isError, error } = useQuery({
    queryKey: ["incident", incidentId],
    queryFn: () => api.incidents.get(incidentId!),
    enabled: !!incidentId,
    refetchInterval: 5000,
    placeholderData: keepPreviousData,
  });

  const { data: runbook } = useQuery({
    queryKey: ["runbook", incidentId],
    queryFn: () => api.runbooks.forIncident(incidentId!),
    enabled: !!incidentId,
    refetchInterval: 10_000,
  });

  const resolveMutation = useMutation({
    mutationFn: () => api.incidents.update(incident!.id, { status: "resolved" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["incident", incidentId] });
      qc.invalidateQueries({ queryKey: ["incidents"] });
      qc.invalidateQueries({ queryKey: ["runbook", incidentId] });
    },
  });

  const postmortemMutation = useMutation({
    mutationFn: () => api.incidents.postmortem(incident!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["incident", incidentId] });
      setShowPostmortem(true);
    },
  });

  if (!incidentId) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/20 backdrop-blur-[1px] z-40 animate-fade-in"
        onClick={() => setActive(null)}
      />

      {/* Panel */}
      <div className="fixed inset-y-0 right-0 w-[560px] bg-surface border-l border-border flex flex-col z-50 animate-slide-in shadow-panel">

        {/* Severity bar */}
        {incident && (
          <div
            className={clsx(
              "h-1 flex-shrink-0",
              SEVERITY_BAR[incident.severity] ?? SEVERITY_BAR.medium
            )}
          />
        )}

        {/* Header */}
        <div className="flex items-start gap-3 px-5 py-4 border-b border-border flex-shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2 flex-wrap">
              {incident && (
                <Badge severity={incident.severity}>{incident.severity}</Badge>
              )}
              {incident && (
                <Badge status={incident.status}>
                  {incident.status.replace("_", " ")}
                </Badge>
              )}
              {incident?.similar_incident_id && (
                <span className="flex items-center gap-1 text-[10px] text-accent-text bg-accent-muted px-2 py-0.5 rounded-md border border-accent/15 font-medium">
                  <Link size={9} />
                  Similar past incident
                </span>
              )}
              {incident?.storm_size != null && incident.storm_size > 1 && (
                <span className="flex items-center gap-1 text-[10px] text-warning-text bg-warning/10 px-2 py-0.5 rounded-md border border-warning/20 font-medium">
                  <Zap size={9} />
                  Storm ×{incident.storm_size}
                </span>
              )}
            </div>
            <h2 className="text-[14px] font-semibold text-text-1 leading-snug">
              {isLoading ? (
                <div className="h-4 bg-slate-100 rounded w-64 animate-pulse" />
              ) : (
                incident?.title
              )}
            </h2>
            {incident && (
              <p className="text-[12px] text-text-3 mt-1">
                {new Date(incident.started_at).toLocaleString()}
                {incident.resolved_at &&
                  ` → ${new Date(incident.resolved_at).toLocaleString()}`}
              </p>
            )}
          </div>
          <button
            onClick={() => setActive(null)}
            className="flex-shrink-0 p-1.5 rounded-lg text-text-3 hover:text-text-1 hover:bg-raised border border-transparent hover:border-border transition-all"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto">
          {isError && (
            <div className="p-5">
              <QueryErrorState message={getErrorMessage(error)} />
            </div>
          )}

          {incident === undefined && isLoading && (
            <div className="p-5">
              <SkeletonText lines={6} />
            </div>
          )}

          {incident && !isError && !incident.rca_full && (
            <div className="flex items-center gap-3 mx-5 my-4 px-4 py-3.5 bg-accent-muted border border-accent/15 rounded-xl">
              <Loader2 size={14} className="text-accent animate-spin flex-shrink-0" />
              <div>
                <p className="text-[13px] font-semibold text-text-1">
                  AI analysis in progress
                </p>
                <p className="text-[12px] text-text-3 mt-0.5">
                  Root cause analysis will appear here shortly
                </p>
              </div>
            </div>
          )}

          {incident?.rca_full && !isError && (
            <div className="p-5 space-y-5">
              {/* Confidence */}
              {incident.rca_confidence != null && (
                <div className="bg-raised border border-border rounded-xl p-4">
                  <div className="flex items-center justify-between mb-2.5">
                    <span className="text-[12px] font-semibold text-text-2">
                      AI Confidence
                    </span>
                    <span
                      className={clsx(
                        "text-[13px] font-bold tabular-nums",
                        incident.rca_confidence > 0.7 ? "text-success-text" :
                        incident.rca_confidence > 0.4 ? "text-warning-text" :
                        "text-danger-text"
                      )}
                    >
                      {Math.round(incident.rca_confidence * 100)}%
                    </span>
                  </div>
                  <div className="h-2 bg-border rounded-full overflow-hidden">
                    <div
                      className={clsx(
                        "h-full rounded-full transition-all duration-500",
                        incident.rca_confidence > 0.7 ? "bg-success" :
                        incident.rca_confidence > 0.4 ? "bg-warning" : "bg-danger"
                      )}
                      style={{
                        width: `${Math.round(incident.rca_confidence * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              )}

              {/* Quick Commands — extracted from the Diagnostic Commands section */}
              {(() => {
                const cmds = extractCommands(incident.rca_full);
                if (!cmds.length) return null;
                return (
                  <div className="bg-[#1a1a2e] border border-[#2d2d4e] rounded-xl overflow-hidden">
                    <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[#2d2d4e]">
                      <span className="w-2 h-2 rounded-full bg-danger" />
                      <span className="w-2 h-2 rounded-full bg-warning" />
                      <span className="w-2 h-2 rounded-full bg-success" />
                      <span className="ml-2 text-[11px] font-semibold text-[#7c8cf8] uppercase tracking-wider">
                        Diagnostic Commands
                      </span>
                    </div>
                    <div className="divide-y divide-[#2d2d4e]">
                      {cmds.map((cmd, i) => (
                        <div key={i} className="group relative px-4 py-3">
                          <pre className="text-[12px] text-[#a5f3fc] font-mono whitespace-pre-wrap leading-relaxed pr-8">
                            {cmd}
                          </pre>
                          <CopyButton text={cmd} alwaysVisible />
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {/* RCA content */}
              <div className="prose-content max-w-none">
                <ReactMarkdown components={mdComponents as object}>{incident.rca_full}</ReactMarkdown>
              </div>

              {/* Cited files */}
              {incident.cited_knowledge?.length > 0 && (
                <div className="bg-raised border border-border rounded-xl p-4">
                  <h3 className="text-[11px] font-semibold uppercase tracking-wider text-text-3 mb-3">
                    IaC files referenced
                  </h3>
                  <ul className="space-y-1.5">
                    {incident.cited_knowledge.map((f, i) => (
                      <li key={i} className="flex items-center gap-2">
                        <FileCode size={12} className="text-accent flex-shrink-0" />
                        <code className="text-[12px] text-text-2 font-mono">{f}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Runbook */}
              {runbook && (
                <div className="bg-raised border border-border rounded-xl overflow-hidden">
                  <button
                    onClick={() => setShowRunbook((v) => !v)}
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface/50 transition-colors"
                  >
                    <div className="flex items-center gap-2">
                      <BookOpen size={13} className="text-accent" />
                      <span className="text-[13px] font-semibold text-text-1">Runbook</span>
                      <span className="text-[11px] text-text-3 truncate max-w-[220px]">{runbook.title}</span>
                    </div>
                    {showRunbook ? <ChevronUp size={14} className="text-text-3" /> : <ChevronDown size={14} className="text-text-3" />}
                  </button>
                  {showRunbook && (
                    <div className="px-4 pb-4 border-t border-border prose-content max-w-none">
                      <ReactMarkdown>{runbook.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              )}

              {incident.status === "resolved" && !runbook && (
                <div className="flex items-center gap-2 px-4 py-3 bg-raised border border-border rounded-xl text-[12px] text-text-3">
                  <Loader2 size={12} className="animate-spin flex-shrink-0" />
                  Generating runbook…
                </div>
              )}

              {/* Post-mortem */}
              {incident.status === "resolved" && (
                <div className="bg-raised border border-border rounded-xl overflow-hidden">
                  <button
                    onClick={() => {
                      if (incident.postmortem) {
                        setShowPostmortem((v) => !v);
                      } else {
                        postmortemMutation.mutate();
                      }
                    }}
                    disabled={postmortemMutation.isPending}
                    className="w-full flex items-center justify-between px-4 py-3 hover:bg-surface/50 transition-colors disabled:opacity-60"
                  >
                    <div className="flex items-center gap-2">
                      <ScrollText size={13} className="text-text-3" />
                      <span className="text-[13px] font-semibold text-text-1">Post-mortem</span>
                      {!incident.postmortem && !postmortemMutation.isPending && (
                        <span className="text-[11px] text-text-3">Click to generate</span>
                      )}
                      {postmortemMutation.isPending && (
                        <Loader2 size={11} className="text-accent animate-spin" />
                      )}
                    </div>
                    {incident.postmortem && (
                      showPostmortem ? <ChevronUp size={14} className="text-text-3" /> : <ChevronDown size={14} className="text-text-3" />
                    )}
                  </button>
                  {showPostmortem && incident.postmortem && (
                    <div className="px-4 pb-4 border-t border-border prose-content max-w-none">
                      <ReactMarkdown components={mdComponents as object}>{incident.postmortem}</ReactMarkdown>
                    </div>
                  )}
                  {postmortemMutation.isError && (
                    <p className="px-4 pb-3 text-[12px] text-danger">{getErrorMessage(postmortemMutation.error)}</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {incident?.status === "open" && (
          <div className="flex-shrink-0 p-4 border-t border-border bg-raised">
            <button
              onClick={() => resolveMutation.mutate()}
              disabled={resolveMutation.isPending}
              className="w-full flex items-center justify-center gap-2 py-2.5 px-4 bg-success text-white hover:bg-success-text rounded-xl text-[13px] font-semibold transition-all shadow-sm hover:shadow-md disabled:opacity-50"
            >
              <CheckCircle size={14} />
              {resolveMutation.isPending ? "Resolving…" : "Mark as Resolved"}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
