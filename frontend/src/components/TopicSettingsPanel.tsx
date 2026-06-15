import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Clock,
  Eraser,
  ExternalLink,
  Github,
  Loader2,
  Play,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
import type { Schedule, Topic, TopicNode } from "../lib/types";
import type { IssueContextState } from "../lib/useIssueContext";
import { Markdown } from "./Markdown";
import {
  defaultRecurrence,
  recurrenceFromSchedule,
  recurrenceToPayload,
  RecurrenceEditor,
  type RecurrenceValue,
} from "./RecurrenceEditor";

type Tab = "settings" | "context";

interface Props {
  topic: Topic;
  tree: TopicNode[];
  context: IssueContextState;
  onClose: () => void;
  onSaved: (topic: Topic) => void;
  onDeleted: () => void;
  onCleared?: () => void;
  initialTab?: Tab;
}

export function TopicSettingsPanel({
  topic,
  tree,
  context,
  onClose,
  onSaved,
  onDeleted,
  onCleared,
  initialTab = "settings",
}: Props) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const [title, setTitle] = useState(topic.title);
  const [slug, setSlug] = useState(topic.slug);
  const [description, setDescription] = useState(topic.description ?? "");
  const [parentId, setParentId] = useState<number | "">(
    topic.parent_id === null ? "" : topic.parent_id,
  );
  const [repo, setRepo] = useState(topic.github_repo ?? "");
  const [issueNumber, setIssueNumber] = useState(
    topic.github_issue_number !== null ? String(topic.github_issue_number) : "",
  );
  const [defaultRepo, setDefaultRepo] = useState("");
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settings = useSettings();
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;

  // Scheduled topics (kind === "scheduled") swap the GitHub/parent/description
  // fields for a task prompt + recurrence editor sourced from their schedule.
  const isScheduled = topic.kind === "scheduled";
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [prompt, setPrompt] = useState("");
  const [recurrence, setRecurrence] = useState<RecurrenceValue>(defaultRecurrence);
  const [enabled, setEnabled] = useState(true);
  const [clearContext, setClearContext] = useState(false);
  const [runningNow, setRunningNow] = useState(false);

  useEffect(() => {
    if (!isScheduled) return;
    void (async () => {
      try {
        const s = await api.getSchedule(topic.id);
        setSchedule(s);
        setPrompt(s.prompt);
        setRecurrence(recurrenceFromSchedule(s));
        setEnabled(s.enabled);
        setClearContext(s.clear_context);
      } catch {
        /* schedule may have been deleted elsewhere */
      }
    })();
  }, [isScheduled, topic.id]);

  // If the user lands on the Context tab while it's unavailable (feature off,
  // or a scheduled topic which has no issue), fall back to the Settings tab.
  useEffect(() => {
    if ((!issueAssociationsEnabled || isScheduled) && tab === "context") {
      setTab("settings");
    }
  }, [issueAssociationsEnabled, isScheduled, tab]);

  useEffect(() => {
    void (async () => {
      try {
        const s = await api.getSettings();
        setDefaultRepo(s.github_repo);
      } catch {
        /* settings optional */
      }
    })();
  }, []);

  const forbiddenIds = collectDescendantIds(tree, topic.id);

  async function save(): Promise<void> {
    const trimmed = title.trim();
    if (!trimmed || saving) return;
    if (isScheduled && !prompt.trim()) {
      setError("Task prompt is required");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (isScheduled) {
        // Title/slug live on the topic; prompt/interval/enabled on the schedule.
        const updated = await api.updateTopic(topic.id, {
          title: trimmed,
          slug: slug.trim() && slug.trim() !== topic.slug ? slug.trim() : undefined,
        });
        await api.updateSchedule(topic.id, {
          prompt: prompt.trim(),
          ...recurrenceToPayload(recurrence),
          clear_context: clearContext,
          enabled,
        });
        onSaved(updated);
        return;
      }
      const updated = await api.updateTopic(topic.id, {
        title: trimmed,
        slug: slug.trim() && slug.trim() !== topic.slug ? slug.trim() : undefined,
        description: description.trim() ? description.trim() : null,
        parent_id: parentId === "" ? null : parentId,
        github_repo: repo.trim() ? repo.trim() : null,
        github_issue_number: issueNumber.trim() ? Number(issueNumber.trim()) : null,
      });
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function runNow(): Promise<void> {
    if (runningNow) return;
    setRunningNow(true);
    setError(null);
    try {
      setSchedule(await api.runScheduleNow(topic.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunningNow(false);
    }
  }

  async function remove(): Promise<void> {
    if (!window.confirm(`Delete "${topic.title}" and all its messages?`)) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteTopic(topic.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDeleting(false);
    }
  }

  async function clearChat(): Promise<void> {
    if (!window.confirm("Erase the entire chat transcript for this topic?")) return;
    setClearing(true);
    setError(null);
    try {
      await api.clearMessages(topic.id);
      onCleared?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setClearing(false);
    }
  }

  async function archive(): Promise<void> {
    if (archiving) return;
    setArchiving(true);
    setError(null);
    try {
      await api.archiveTopic(topic.id);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setArchiving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-[min(480px,100%)] h-full bg-bg border-l border-border flex flex-col">
        <header className="flex items-center justify-between px-4 h-12 border-b border-border">
          <h2 className="font-semibold truncate">
            {isScheduled ? "Scheduled topic" : "Topic settings"}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-surface"
            aria-label="Close"
            data-tooltip="Close"
          >
            <X size={18} />
          </button>
        </header>

        <nav className="flex border-b border-border text-sm">
          <TabButton active={tab === "settings"} onClick={() => setTab("settings")}>
            Settings
          </TabButton>
          {issueAssociationsEnabled && !isScheduled && (
            <TabButton
              active={tab === "context"}
              onClick={() => setTab("context")}
              accent
            >
              <Github size={14} />
              Context
            </TabButton>
          )}
        </nav>

        <div className="flex-1 overflow-y-auto">
          {tab === "settings" ? (
            <div className="p-4 space-y-4">
              <section>
                <label className="block text-xs text-muted mb-1">Title</label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                />
              </section>

              <section>
                <label className="block text-xs text-muted mb-1">
                  Slug
                </label>
                <input
                  type="text"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent font-mono"
                />
                <p className="text-[11px] text-muted mt-1">
                  URL fragment for this topic — share or bookmark{" "}
                  <code className="font-mono">#{slug || topic.slug}</code>{" "}
                  to deep-link here. The server normalizes the value and
                  appends <code>-2</code>, <code>-3</code>… on collision.
                </p>
              </section>

              <section>
                <label className="block text-xs text-muted mb-1">
                  {isScheduled ? "Task prompt" : "Description"}
                </label>
                {isScheduled ? (
                  <textarea
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    rows={4}
                    placeholder="What should the assistant do on every run?"
                    className="w-full resize-none bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                  />
                ) : (
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    rows={4}
                    className="w-full resize-none bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                  />
                )}
                {isScheduled && (
                  <p className="text-[11px] text-muted mt-1">
                    Sent as the message on each scheduled run.
                  </p>
                )}
              </section>

              {isScheduled ? (
                <section className="space-y-3">
                  <RecurrenceEditor value={recurrence} onChange={setRecurrence} />
                  <label className="flex items-center gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      checked={enabled}
                      onChange={(e) => setEnabled(e.target.checked)}
                    />
                    <span>Enabled</span>
                  </label>
                  <label className="flex items-start gap-2 text-sm cursor-pointer">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={clearContext}
                      onChange={(e) => setClearContext(e.target.checked)}
                    />
                    <span>
                      Clear context before each run
                      <span className="block text-[11px] text-muted">
                        Wipes the topic's prior messages so every run starts fresh.
                      </span>
                    </span>
                  </label>
                  {schedule && (
                    <div className="rounded border border-border bg-surface/50 px-3 py-2 text-[11px] text-muted space-y-1">
                      <div className="flex items-center gap-1.5">
                        <Clock size={11} />
                        Status: <span className="text-text">{schedule.status}</span>
                      </div>
                      {schedule.next_run_at && (
                        <div>Next run: {new Date(schedule.next_run_at).toLocaleString()}</div>
                      )}
                      {schedule.last_run_at && (
                        <div>Last run: {new Date(schedule.last_run_at).toLocaleString()}</div>
                      )}
                      {schedule.last_error && (
                        <div className="text-red-500">Last error: {schedule.last_error}</div>
                      )}
                    </div>
                  )}
                  <button
                    onClick={() => void runNow()}
                    disabled={runningNow || schedule?.status === "running"}
                    className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-sm hover:bg-surface disabled:opacity-50"
                  >
                    <Play size={14} />
                    {runningNow ? "Starting…" : "Run now"}
                  </button>
                </section>
              ) : (
                <>
                  <section>
                    <label className="block text-xs text-muted mb-1">Parent topic</label>
                    <select
                      value={parentId === "" ? "" : String(parentId)}
                      onChange={(e) =>
                        setParentId(e.target.value === "" ? "" : Number(e.target.value))
                      }
                      className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                    >
                      <option value="">— top level —</option>
                      {flatten(tree).map((opt) => (
                        <option
                          key={opt.id}
                          value={opt.id}
                          disabled={forbiddenIds.has(opt.id)}
                        >
                          {"\u00A0".repeat(opt.depth * 2)}
                          {opt.title}
                        </option>
                      ))}
                    </select>
                  </section>

                  {issueAssociationsEnabled && (
                    <section className="grid grid-cols-[1fr_120px] gap-2">
                      <div>
                        <label className="block text-xs text-muted mb-1">GitHub repo</label>
                        <input
                          type="text"
                          value={repo}
                          onChange={(e) => setRepo(e.target.value)}
                          placeholder={defaultRepo || "owner/name"}
                          className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                        />
                      </div>
                      <div>
                        <label className="block text-xs text-muted mb-1">Issue #</label>
                        <input
                          type="number"
                          value={issueNumber}
                          onChange={(e) => setIssueNumber(e.target.value)}
                          placeholder="123"
                          className="w-full bg-surface border border-border rounded px-2 py-1.5 text-sm outline-none focus:border-accent"
                        />
                      </div>
                    </section>
                  )}
                </>
              )}

              {error && <p className="text-xs text-red-500">{error}</p>}

              <section className="pt-2 border-t border-border space-y-3">
                <div>
                  <button
                    onClick={() => void clearChat()}
                    disabled={clearing}
                    className="flex items-center gap-2 text-sm text-amber-500 hover:text-amber-400 disabled:opacity-50"
                  >
                    <Eraser size={14} />
                    {clearing ? "Clearing…" : "Clear chat"}
                  </button>
                  <p className="text-[11px] text-muted mt-1">
                    Erases every message in this topic. The topic itself and its GitHub link are kept.
                  </p>
                </div>
                {!isScheduled && (
                  <div>
                    <button
                      onClick={() => void archive()}
                      disabled={archiving}
                      className="flex items-center gap-2 text-sm text-muted hover:text-text disabled:opacity-50"
                    >
                      <Archive size={14} />
                      {archiving ? "Archiving…" : "Archive topic"}
                    </button>
                    <p className="text-[11px] text-muted mt-1">
                      Hides the topic from the sidebar tree but keeps its messages, GitHub link, and parent intact. Restore it any time from the archive (click your profile in the sidebar).
                    </p>
                  </div>
                )}
                <div>
                  <button
                    onClick={() => void remove()}
                    disabled={deleting}
                    className="flex items-center gap-2 text-sm text-red-500 hover:text-red-400 disabled:opacity-50"
                  >
                    <Trash2 size={14} />
                    {deleting ? "Deleting…" : "Delete topic"}
                  </button>
                  <p className="text-[11px] text-muted mt-1">
                    Removes the topic and all its messages. Child topics become roots.
                  </p>
                </div>
              </section>
            </div>
          ) : (
            <ContextTab topic={topic} context={context} onSwitchToSettings={() => setTab("settings")} />
          )}
        </div>

        {tab === "settings" && (
          <footer className="border-t border-border p-3 flex justify-end gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
            >
              Cancel
            </button>
            <button
              onClick={() => void save()}
              disabled={!title.trim() || (isScheduled && !prompt.trim()) || saving}
              className="px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </footer>
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  accent,
  children,
  onClick,
}: {
  active: boolean;
  accent?: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  const base =
    "flex-1 px-3 py-2 inline-flex items-center justify-center gap-1.5 border-b-2 transition-colors";
  const cls = active
    ? accent
      ? "border-accent text-accent bg-accent/5"
      : "border-accent text-accent"
    : "border-transparent text-muted hover:text-text";
  return (
    <button onClick={onClick} className={`${base} ${cls}`}>
      {children}
    </button>
  );
}

function ContextTab({
  topic,
  context,
  onSwitchToSettings,
}: {
  topic: Topic;
  context: IssueContextState;
  onSwitchToSettings: () => void;
}) {
  const { status, summary, error, effectiveRepo, hasIssue, creating, pushing, refresh, createAndLink, pushToIssue } =
    context;

  return (
    <div className="p-4 space-y-4">
      <div className="rounded-lg border border-accent/30 bg-accent/5 p-3 space-y-3">
        <div className="flex items-center gap-2 text-sm">
          <Github size={16} className="text-accent" />
          <span className="font-medium text-accent">GitHub issue context</span>
        </div>

        {!hasIssue && (
          <div className="text-sm space-y-2">
            <p className="text-muted">
              This topic isn't linked to a GitHub issue yet. Create one now, or set
              the issue number in Settings.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => void createAndLink()}
                disabled={creating}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-accent text-white text-xs disabled:opacity-50"
              >
                {creating ? <Loader2 size={12} className="animate-spin" /> : <Github size={12} />}
                {creating ? "Creating…" : "Create issue from topic"}
              </button>
              <button
                onClick={onSwitchToSettings}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg"
              >
                Link existing issue
              </button>
            </div>
          </div>
        )}

        {hasIssue && status === "no-repo" && (
          <div className="text-sm space-y-2">
            <div className="flex items-start gap-2 text-amber-500">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>
                Issue #{topic.github_issue_number} is set, but no repository is
                configured for this topic and no global default exists.
              </span>
            </div>
            <button
              onClick={onSwitchToSettings}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg"
            >
              Set repo on topic
            </button>
          </div>
        )}

        {hasIssue && status !== "no-repo" && (
          <div className="space-y-3">
            <div className="flex items-baseline gap-2 text-sm">
              <span className="font-mono text-xs text-muted truncate">
                {effectiveRepo}#{topic.github_issue_number}
              </span>
              {summary?.issue_url && (
                <a
                  href={summary.issue_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-accent hover:underline inline-flex items-center gap-0.5 shrink-0"
                >
                  view <ExternalLink size={10} />
                </a>
              )}
            </div>

            {summary?.issue_title && (
              <div className="text-sm font-medium leading-snug">
                {summary.issue_title}
              </div>
            )}

            <div className="flex items-center gap-2 flex-wrap">
              <ActionButton
                onClick={() => void refresh({ force: true })}
                disabled={status === "loading"}
                icon={
                  status === "loading" ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <RefreshCw size={12} />
                  )
                }
                label={summary ? "Sync issue" : "Load issue"}
                title="Force refresh from GitHub and regenerate the summary"
              />
              <ActionButton
                onClick={() => void pushToIssue()}
                disabled={pushing}
                icon={pushing ? <Loader2 size={12} className="animate-spin" /> : <Upload size={12} />}
                label={pushing ? "Pushing…" : "Update issue"}
                title="Update the GitHub issue's title and body to match this topic"
              />
              <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted">
                {status === "loading" && (
                  <>
                    <Loader2 size={12} className="animate-spin" /> Refreshing…
                  </>
                )}
                {status === "ready" && (
                  <>
                    <CheckCircle2 size={12} className="text-green-500" /> Up to date
                  </>
                )}
                {status === "error" && (
                  <>
                    <AlertTriangle size={12} className="text-red-500" /> Error
                  </>
                )}
              </span>
            </div>

            {summary && (
              <p className="text-[11px] text-muted">
                {summary.cached ? "Cached" : "Fetched"} {formatRelative(summary.fetched_at)}
                {" · summarised by "}
                {summary.model}
              </p>
            )}

            {error && (
              <p className="text-xs text-red-500 break-words">{error}</p>
            )}

            {summary && (
              <div>
                <Markdown className="text-sm leading-relaxed bg-bg border border-border rounded p-2">
                  {summary.summary}
                </Markdown>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ActionButton({
  onClick,
  disabled,
  icon,
  label,
  title,
}: {
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
  title?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-accent/40 bg-bg text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
    >
      {icon}
      {label}
    </button>
  );
}

function flatten(
  tree: TopicNode[],
  depth = 0,
  out: { id: number; title: string; depth: number }[] = [],
): { id: number; title: string; depth: number }[] {
  for (const node of tree) {
    out.push({ id: node.id, title: node.title, depth });
    if (node.children.length) flatten(node.children, depth + 1, out);
  }
  return out;
}

function collectDescendantIds(tree: TopicNode[], rootId: number): Set<number> {
  const forbidden = new Set<number>([rootId]);
  const walk = (nodes: TopicNode[], collecting: boolean): void => {
    for (const n of nodes) {
      const next = collecting || n.id === rootId;
      if (next) forbidden.add(n.id);
      if (n.children.length) walk(n.children, next);
    }
  };
  walk(tree, false);
  return forbidden;
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "just now";
  const diffMs = Date.now() - then;
  if (diffMs < 60_000) return "just now";
  const mins = Math.round(diffMs / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
