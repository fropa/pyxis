import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import { FlaskConical, Loader2, Sparkles, RotateCcw } from "lucide-react";
import { api } from "../api/client";
import clsx from "clsx";

const PLACEHOLDER = `# Paste your logs here — any format works
2026-04-05 09:12:44 ERROR [api-gateway] upstream connect error or disconnect/reset before headers. reset reason: connection failure, transport failure reason: delayed connect error: 111
2026-04-05 09:12:45 ERROR [api-gateway] upstream connect error or disconnect/reset before headers. reset reason: connection failure
2026-04-05 09:12:46 WARN  [auth-service] database connection pool exhausted, waiting...
2026-04-05 09:12:50 ERROR [auth-service] failed to acquire connection after 4000ms
2026-04-05 09:12:51 ERROR [api-gateway] 503 Service Unavailable: auth-service`;

export default function PlaygroundView() {
  const [logs, setLogs] = useState("");
  const [context, setContext] = useState("");

  const { mutate, data, isPending, reset } = useMutation({
    mutationFn: () => api.analyze.logs({ logs, context: context || undefined }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 space-y-6 max-w-[900px]">

        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center">
            <FlaskConical size={16} className="text-accent-text" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-text-1">Log Anomaly Playground</h1>
            <p className="text-[13px] text-text-3 mt-0.5">
              Paste any logs — Claude will analyze them for anomalies and root causes
            </p>
          </div>
        </div>

        <div className={clsx("grid gap-6", data ? "grid-cols-2" : "grid-cols-1")}>

          {/* Input panel */}
          <div className="space-y-4">
            <div>
              <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
                Logs <span className="text-text-4 font-normal">(any format — syslog, JSON, k8s, etc.)</span>
              </label>
              <textarea
                value={logs}
                onChange={(e) => setLogs(e.target.value)}
                placeholder={PLACEHOLDER}
                rows={16}
                className={clsx(
                  "w-full bg-white dark:bg-raised border border-border rounded-xl px-4 py-3",
                  "text-[12px] text-[#030712] dark:text-white font-mono resize-none",
                  "placeholder:text-text-4 focus:outline-none focus:border-accent/50",
                  "focus:ring-2 focus:ring-accent/10 transition-all"
                )}
              />
            </div>

            <div>
              <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
                Additional context <span className="text-text-4 font-normal">(optional)</span>
              </label>
              <textarea
                value={context}
                onChange={(e) => setContext(e.target.value)}
                placeholder="e.g. This is a Kubernetes cluster running auth-service v2.3.1. We just deployed 10 minutes ago."
                rows={3}
                className={clsx(
                  "w-full bg-white dark:bg-raised border border-border rounded-xl px-4 py-3",
                  "text-[13px] text-[#030712] dark:text-white resize-none",
                  "placeholder:text-text-4 focus:outline-none focus:border-accent/50",
                  "focus:ring-2 focus:ring-accent/10 transition-all"
                )}
              />
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => mutate()}
                disabled={!logs.trim() || isPending}
                className="flex items-center gap-2 px-5 py-2.5 bg-accent hover:bg-accent-hover text-white rounded-xl text-[13px] font-semibold transition-all shadow-sm hover:shadow-md disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isPending ? (
                  <><Loader2 size={14} className="animate-spin" /> Analyzing…</>
                ) : (
                  <><Sparkles size={14} /> Analyze with Claude</>
                )}
              </button>
              {data && (
                <button
                  onClick={() => { reset(); setLogs(""); setContext(""); }}
                  className="flex items-center gap-2 px-4 py-2.5 text-text-3 hover:text-text-1 hover:bg-raised border border-border rounded-xl text-[13px] font-medium transition-all"
                >
                  <RotateCcw size={13} />
                  Reset
                </button>
              )}
            </div>
          </div>

          {/* Results panel */}
          {data && (
            <div className="bg-surface border border-border rounded-xl overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                <div className="flex items-center gap-2">
                  <Sparkles size={13} className="text-accent" />
                  <span className="text-[13px] font-semibold text-text-1">Analysis</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-text-3">Confidence</span>
                  <span className={clsx(
                    "text-[12px] font-bold tabular-nums",
                    data.confidence > 0.7 ? "text-success-text" :
                    data.confidence > 0.4 ? "text-warning-text" : "text-danger-text"
                  )}>
                    {Math.round(data.confidence * 100)}%
                  </span>
                </div>
              </div>
              <div className="p-4 overflow-y-auto max-h-[600px] prose-content">
                <ReactMarkdown>{data.analysis}</ReactMarkdown>
              </div>
            </div>
          )}

          {/* Empty state */}
          {!data && !isPending && (
            <div className="hidden" />
          )}
        </div>

      </div>
    </div>
  );
}
