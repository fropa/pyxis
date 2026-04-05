import { useState } from "react";
import { useQuery, useMutation, useQueryClient, keepPreviousData } from "@tanstack/react-query";
import {
  GitBranch, Plus, RefreshCw, CheckCircle, AlertCircle, Clock,
  Bell, Trash2, Key, MessageSquare, Link2,
} from "lucide-react";
import { api, KnowledgeSource, NotificationChannel } from "../api/client";
import { useAppStore } from "../store";
import clsx from "clsx";

// ── Local primitives ───────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[12px] font-semibold text-text-2 mb-1.5">
        {label}
      </label>
      {children}
    </div>
  );
}

const inputCls =
  "w-full bg-white dark:bg-raised border border-border rounded-lg px-3 py-2 text-[13px] " +
  "text-[#030712] dark:text-white font-medium placeholder:text-text-4 focus:outline-none " +
  "focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all";

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={inputCls} {...props} />;
}

function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text-1",
        "focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all"
      )}
      {...props}
    />
  );
}

function Btn({
  children,
  className,
  variant = "primary",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
}) {
  return (
    <button
      className={clsx(
        "flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        variant === "primary"
          ? "bg-accent hover:bg-accent-hover text-white shadow-sm hover:shadow-md"
          : "text-text-3 hover:text-text-1 hover:bg-raised border border-border",
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function Card({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={clsx(
        "bg-surface border border-border rounded-xl shadow-card",
        className
      )}
    >
      {children}
    </div>
  );
}

function CardHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: React.ElementType;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex items-start gap-3 p-5 border-b border-border">
      <div className="w-9 h-9 rounded-xl bg-accent-muted border border-accent/15 flex items-center justify-center flex-shrink-0">
        <Icon size={16} className="text-accent-text" />
      </div>
      <div className="min-w-0">
        <h2 className="text-[14px] font-semibold text-text-1">{title}</h2>
        {description && (
          <p className="text-[12px] text-text-3 mt-0.5 leading-relaxed">
            {description}
          </p>
        )}
      </div>
    </div>
  );
}

// ── Status configs ─────────────────────────────────────────────────────────────

const IDX_CFG: Record<
  string,
  { icon: React.ElementType; label: string; color: string; bg: string }
> = {
  ready:    { icon: CheckCircle, label: "Ready",    color: "text-success-text",  bg: "bg-success-bg" },
  error:    { icon: AlertCircle, label: "Error",    color: "text-danger-text",   bg: "bg-danger-bg"  },
  indexing: { icon: RefreshCw,   label: "Indexing", color: "text-accent-text",   bg: "bg-accent-muted" },
  pending:  { icon: Clock,       label: "Pending",  color: "text-text-3",        bg: "bg-raised"     },
};

function SourceRow({
  source,
  onReindex,
}: {
  source: KnowledgeSource;
  onReindex: (id: string) => void;
}) {
  const cfg = IDX_CFG[source.index_status] ?? IDX_CFG.pending;
  const Icon = cfg.icon;

  return (
    <div className="flex items-center gap-3 p-3.5 bg-bg border border-border rounded-xl hover:border-border-strong transition-colors">
      <div className="w-8 h-8 rounded-lg bg-surface border border-border flex items-center justify-center flex-shrink-0">
        <GitBranch size={13} className="text-text-3" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-medium text-text-1 truncate">{source.repo_url}</p>
        <p className="text-[11px] text-text-3 capitalize">{source.repo_type}</p>
        {source.error_message && (
          <p className="text-[11px] text-danger-text mt-0.5 truncate">
            {source.error_message}
          </p>
        )}
      </div>
      <span
        className={clsx(
          "flex items-center gap-1.5 text-[11px] font-semibold px-2 py-1 rounded-md flex-shrink-0",
          cfg.bg,
          cfg.color
        )}
      >
        <Icon size={11} className={source.index_status === "indexing" ? "animate-spin" : ""} />
        {cfg.label}
      </span>
      {source.last_indexed_at && (
        <span className="text-[11px] text-text-4 flex-shrink-0">
          {new Date(source.last_indexed_at).toLocaleDateString()}
        </span>
      )}
      <button
        onClick={() => onReindex(source.id)}
        className="flex items-center gap-1 text-[11px] text-text-3 hover:text-accent-text font-medium transition-colors flex-shrink-0"
      >
        <RefreshCw size={11} />
        Re-index
      </button>
    </div>
  );
}

function ChannelRow({
  channel,
  onDelete,
}: {
  channel: NotificationChannel;
  onDelete: (id: string) => void;
}) {
  const KindIcon = channel.kind === "slack" ? MessageSquare : Link2;

  return (
    <div className="flex items-center gap-3 p-3.5 bg-bg border border-border rounded-xl">
      <div className="w-8 h-8 rounded-lg bg-accent-muted border border-accent/15 flex items-center justify-center flex-shrink-0">
        <KindIcon size={13} className="text-accent-text" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-semibold text-text-1">{channel.name}</p>
        <p className="text-[11px] text-text-3 capitalize">
          {channel.kind} · min severity: {channel.min_severity}
        </p>
      </div>
      <button
        onClick={() => onDelete(channel.id)}
        className="p-1.5 rounded-lg text-text-4 hover:text-danger hover:bg-danger-bg border border-transparent hover:border-danger-border transition-all"
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function OnboardingView() {
  const { apiKey, setApiKey } = useAppStore();
  const [localKey, setLocalKey] = useState(apiKey);
  const [repoUrl, setRepoUrl] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [repoType, setRepoType] = useState("github");
  const [slackUrl, setSlackUrl] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");

  const qc = useQueryClient();

  const { data: sources = [] } = useQuery({
    queryKey: ["knowledge-sources"],
    queryFn: api.knowledge.listSources,
    enabled: !!apiKey,
    refetchInterval: 8000,
    placeholderData: keepPreviousData,
  });

  const addMutation = useMutation({
    mutationFn: api.knowledge.addSource,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge-sources"] });
      setRepoUrl("");
      setAccessToken("");
    },
  });

  const reindexMutation = useMutation({
    mutationFn: api.knowledge.reindex,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge-sources"] }),
  });

  const { data: channels = [] } = useQuery({
    queryKey: ["notification-channels"],
    queryFn: api.notifications.list,
    enabled: !!apiKey,
  });

  const addChannelMutation = useMutation({
    mutationFn: api.notifications.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-channels"] }),
  });

  const deleteChannelMutation = useMutation({
    mutationFn: api.notifications.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notification-channels"] }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-8 space-y-6">
        {/* Page header */}
        <div>
          <h1 className="text-xl font-semibold text-text-1">Settings</h1>
          <p className="text-[13px] text-text-3 mt-1">
            Configure your API key, connect repositories for AI training, and set up alert channels.
          </p>
        </div>

        {/* ── API Key ── */}
        <Card>
          <CardHeader
            icon={Key}
            title="API Key"
            description="Your key authenticates the InfraWatch agent and the dashboard."
          />
          <div className="p-5 space-y-3">
            <div className="flex gap-2">
              <Input
                type="password"
                value={localKey}
                onChange={(e) => setLocalKey(e.target.value)}
                placeholder="iw-live-xxxxxxxxxxxxxxxxxxxx"
                className="flex-1"
              />
              <Btn onClick={() => setApiKey(localKey)}>Save</Btn>
            </div>
            {apiKey ? (
              <p className="flex items-center gap-1.5 text-[12px] text-success-text font-medium">
                <CheckCircle size={13} />
                Connected · key ending ···{apiKey.slice(-8)}
              </p>
            ) : (
              <p className="text-[12px] text-text-3">
                Enter your key from the InfraWatch portal.
              </p>
            )}
          </div>
        </Card>

        {/* ── IaC Knowledge Base ── */}
        {apiKey && (
          <Card>
            <CardHeader
              icon={GitBranch}
              title="IaC Knowledge Base"
              description="Index your Helm charts, Terraform, Ansible playbooks, and CI/CD configs. The AI will cite specific files when explaining root causes."
            />
            <div className="p-5 space-y-4">
              <div className="space-y-3">
                <div className="flex gap-2">
                  <Select
                    value={repoType}
                    onChange={(e) => setRepoType(e.target.value)}
                  >
                    <option value="github">GitHub</option>
                    <option value="gitlab">GitLab</option>
                    <option value="gitea">Gitea</option>
                  </Select>
                  <Input
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    placeholder="https://github.com/your-org/infra-repo"
                    className="flex-1"
                  />
                </div>
                <Input
                  type="password"
                  value={accessToken}
                  onChange={(e) => setAccessToken(e.target.value)}
                  placeholder="Access token (for private repos)"
                />
              </div>
              <Btn
                onClick={() =>
                  addMutation.mutate({
                    repo_url: repoUrl,
                    repo_type: repoType,
                    access_token: accessToken || undefined,
                  })
                }
                disabled={!repoUrl || addMutation.isPending}
              >
                <Plus size={14} />
                {addMutation.isPending ? "Adding…" : "Add & Index"}
              </Btn>

              {sources.length > 0 && (
                <div className="space-y-2 pt-2 border-t border-border">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-text-4 pt-1">
                    Indexed repositories
                  </p>
                  {sources.map((s) => (
                    <SourceRow key={s.id} source={s} onReindex={reindexMutation.mutate} />
                  ))}
                </div>
              )}
            </div>
          </Card>
        )}

        {/* ── Notifications ── */}
        {apiKey && (
          <Card>
            <CardHeader
              icon={Bell}
              title="Alert Channels"
              description="Get notified in Slack or via webhook when incidents open and AI analysis is ready."
            />
            <div className="p-5 space-y-4">
              <Field label="Slack webhook URL">
                <div className="flex gap-2 mt-1.5">
                  <Input
                    value={slackUrl}
                    onChange={(e) => setSlackUrl(e.target.value)}
                    placeholder="https://hooks.slack.com/services/…"
                    className="flex-1"
                  />
                  <Btn
                    onClick={() => {
                      addChannelMutation.mutate({
                        name: "Slack",
                        kind: "slack",
                        config: { webhook_url: slackUrl },
                      });
                      setSlackUrl("");
                    }}
                    disabled={!slackUrl || addChannelMutation.isPending}
                  >
                    <MessageSquare size={13} />
                    Add
                  </Btn>
                </div>
              </Field>

              <Field label="Webhook URL">
                <div className="flex gap-2 mt-1.5">
                  <Input
                    value={webhookUrl}
                    onChange={(e) => setWebhookUrl(e.target.value)}
                    placeholder="PagerDuty, OpsGenie, custom endpoint…"
                    className="flex-1"
                  />
                  <Btn
                    onClick={() => {
                      addChannelMutation.mutate({
                        name: "Webhook",
                        kind: "webhook",
                        config: { url: webhookUrl, headers: {} },
                      });
                      setWebhookUrl("");
                    }}
                    disabled={!webhookUrl || addChannelMutation.isPending}
                  >
                    <Link2 size={13} />
                    Add
                  </Btn>
                </div>
              </Field>

              {channels.length > 0 && (
                <div className="space-y-2 pt-2 border-t border-border">
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-text-4 pt-1">
                    Configured channels
                  </p>
                  {channels.map((ch) => (
                    <ChannelRow
                      key={ch.id}
                      channel={ch}
                      onDelete={deleteChannelMutation.mutate}
                    />
                  ))}
                </div>
              )}
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
