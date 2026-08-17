import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Code2,
  Copy,
  CornerDownRight,
  Database,
  ExternalLink,
  Eye,
  HelpCircle,
  History,
  Link2,
  Loader2,
  Package,
  PauseCircle,
  PlayCircle,
  Plus,
  Radar,
  RotateCcw,
  Settings as SettingsIcon,
  ShieldCheck,
  ShieldQuestion,
  Trash2,
  Webhook,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import { mcpAuthStore } from "../lib/mcpAuth";
import { matchAgentSlashCommands, type SlashCommand } from "../lib/commands";
import { useSettings } from "../lib/settingsStore";
import {
  normalizeArtifactMarkdown,
  parseAgentDirectives,
  stripAgentDirectives,
} from "../lib/directives";
import { parseSuggestions, stripSuggestionBlock } from "../lib/suggestions";
import { useAzureSpeech } from "../lib/useAzureSpeech";
import { useResizableHeight } from "../lib/useResizableHeight";
import { Composer } from "./Composer";
import { ComposerModelControls } from "./ComposerModelControls";
import { Markdown } from "./Markdown";
import { HighlightedText } from "../lib/searchHighlight";
import { MessageMeta, formatTimestamp } from "./MessageMeta";
import { SuggestedReplies } from "./SuggestedReplies";
import { TopicPicker } from "./TopicPicker";
import { Select } from "./Select";
import { APPROVAL_POLICIES } from "./AgentsSettings";
import { AgentUsageSection } from "./AgentUsage";
import { PermissionBody } from "./AgentPermissionBody";
import {
  CATEGORY_STYLE,
  classify,
  toolIcon,
  StepConnector,
  HookGutter,
} from "./AgentTimeline";
import type {
  AgentApprovalPolicy,
  AgentArtifact,
  AgentEvent,
  AgentPermissionDecisionValue,
  AgentRun,
  AgentSession,
  AgentState,
  AgentTrigger,
  Collection,
  Me,
  Topic,
} from "../lib/types";

interface AgentViewProps {
  agents: AgentSession[];
  agentId: number | null;
  enabled: boolean;
  available: boolean;
  unavailableReason: string | null;
  /** Re-fetch the session list in the parent (status changed, created, deleted). */
  onReload: () => void;
  /** Select a session (or clear with null to show the start form). */
  onSelect: (id: number | null) => void;
  onOpenSettings: () => void;
  /** Topic to preselect in the new-agent form (e.g. opened from a topic). */
  draftTopicId?: number | null;
}

// One step in the workflow timeline.
type DecisionHandler = (
  requestId: string,
  decision: AgentPermissionDecisionValue,
) => void | Promise<void>;

// A small on/off filter pill for the workflow display toggles.
function ToggleChip({
  active,
  label,
  onClick,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition ${
        active
          ? "border-accent/40 bg-accent/15 text-accent"
          : "border-border text-muted hover:text-fg"
      }`}
    >
      {active ? <Check size={10} /> : <X size={10} className="opacity-50" />}
      {label}
    </button>
  );
}

// Collapsible right-hand panel mirroring the conversation-stats aside on
// topics/chats: a token-usage readout plus the workflow "Show" filters.
// Collapse state is persisted.
const SHOW_PANEL_KEY = "precursor:agent-show-panel:collapsed";

function AgentInsightsPanel({
  showPrefs,
  toggleShow,
  events,
  model,
  agent,
  runs,
  runFilter,
  onRunFilterChange,
}: {
  showPrefs: ShowPrefs;
  toggleShow: (k: keyof ShowPrefs) => void;
  events: AgentEvent[];
  model: string | null;
  agent: AgentSession;
  runs: AgentRun[];
  runFilter: number | null;
  onRunFilterChange: (choice: number | "all") => void;
}) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    // Hidden by default — only stay open when the user explicitly expanded it.
    return window.localStorage.getItem(SHOW_PANEL_KEY) !== "0";
  });
  useEffect(() => {
    window.localStorage.setItem(SHOW_PANEL_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  if (collapsed) {
    return (
      <aside className="flex w-9 shrink-0 flex-col items-center border-l border-border bg-surface/40 px-1 py-2">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="rounded p-1.5 text-muted hover:bg-surface"
          data-tooltip="Show usage & filters"
          aria-label="Show usage and workflow filters"
        >
          <ChevronLeft size={16} />
        </button>
        <BarChart3 size={16} className="mt-2 text-muted" />
        <Eye size={16} className="mt-2 text-muted" />
      </aside>
    );
  }

  return (
    <aside className="flex w-60 shrink-0 flex-col border-l border-border bg-surface/30">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <span>Insights</span>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="rounded p-1 text-muted hover:bg-surface"
          data-tooltip="Collapse panel"
          aria-label="Collapse panel"
        >
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto p-3">
        <AgentUsageSection events={events} model={model} />
        <div className="border-t border-border" />
        <AgentRunsSection agent={agent} runs={runs} selected={runFilter} onSelect={onRunFilterChange} />
        <div className="border-t border-border" />
        <AgentOrchestrationSection agent={agent} />
        <div className="border-t border-border" />
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Eye size={14} />
            <span>Show</span>
          </div>
          <div className="flex flex-col items-start gap-1.5">
            <ToggleChip
              active={showPrefs.system}
              onClick={() => toggleShow("system")}
              label="System"
            />
            <ToggleChip
              active={showPrefs.assistant}
              onClick={() => toggleShow("assistant")}
              label="Assistant"
            />
            <ToggleChip
              active={showPrefs.thinking}
              onClick={() => toggleShow("thinking")}
              label="Thinking"
            />
            <ToggleChip active={showPrefs.tool} onClick={() => toggleShow("tool")} label="Tool" />
            <ToggleChip
              active={showPrefs.lifecycle}
              onClick={() => toggleShow("lifecycle")}
              label="Lifecycle"
            />
          </div>
        </div>
      </div>
    </aside>
  );
}

// Short label for what set a run going. Kept terse — it sits in a 60-wide rail.
const RUN_TRIGGER_LABEL: Record<AgentRun["trigger"], string> = {
  manual: "Manual",
  workflow: "Workflow",
  schedule: "Schedule",
  webhook: "Webhook",
  fleet: "Fleet",
  retry: "Retry",
  replay: "Replay",
};

// Execution history for one agent.
//
// An agent is a reusable *definition*, so it can be driven by several things at
// once — two workflows, a schedule and a manual nudge. Each of those is a run
// with its own status, capability snapshot, artifacts and token spend, and this
// is where that separation becomes visible: without it a shared agent looks like
// a single confusing timeline where concurrent drivers appear to overwrite
// each other.
function AgentRunsSection({
  agent,
  runs,
  selected,
  onSelect,
}: {
  agent: AgentSession;
  runs: AgentRun[];
  selected: number | null;
  onSelect: (choice: number | "all") => void;
}) {
  if (runs.length === 0) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <History size={14} />
          <span>Runs</span>
        </div>
        <p className="text-[11px] text-muted">
          No executions yet. Each start — manual, workflow, schedule or webhook — opens its own run
          so concurrent drivers stay separate.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <History size={14} />
        <span>Runs</span>
        <span className="text-[11px] text-muted">({runs.length})</span>
      </div>
      <button
        type="button"
        onClick={() => onSelect("all")}
        className={`w-full rounded border px-2 py-1 text-left text-[11px] ${
          selected == null
            ? "border-accent/50 bg-accent/10 font-medium"
            : "border-border bg-surface/50 text-muted hover:bg-surface"
        }`}
        data-tooltip="Show every execution in one timeline"
      >
        All runs
      </button>
      {runs.map((run) => {
        const current = agent.current_run?.id === run.id;
        const active = selected === run.id;
        const spend = run.total_input_tokens + run.total_output_tokens;
        return (
          <button
            type="button"
            key={run.id}
            onClick={() => onSelect(active ? "all" : run.id)}
            aria-pressed={active}
            className={`w-full rounded border px-2 py-1 text-left transition-colors ${
              active
                ? "border-accent bg-accent/10"
                : current
                  ? "border-accent/50 bg-accent/5 hover:bg-accent/10"
                  : "border-border bg-surface/50 hover:bg-surface"
            }`}
            data-tooltip={`Run #${run.id} · ${RUN_TRIGGER_LABEL[run.trigger]}${
              run.workflow_run_id ? ` · workflow run #${run.workflow_run_id}` : ""
            }\n${run.total_input_tokens.toLocaleString()} in · ${run.total_output_tokens.toLocaleString()} out\n${
              active ? "Click to show every run" : "Click to read this run on its own"
            }`}
          >
            <div className="flex items-center gap-1.5">
              <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
                {RUN_TRIGGER_LABEL[run.trigger]}
                {run.workflow_run_id ? ` · #${run.workflow_run_id}` : ""}
              </span>
              {current && (
                <span className="shrink-0 rounded bg-accent/20 px-1 text-[10px] text-accent">
                  now
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 text-[10px] text-muted">
              <span className="truncate">{run.status}</span>
              {spend > 0 && <span className="shrink-0">· {spend.toLocaleString()} tok</span>}
            </div>
          </button>
        );
      })}
    </div>
  );
}

// Per-agent orchestration cockpit shown in the insights sidebar: the shared
// artifacts this agent published to the blackboard, its durable cross-run state
// and its external webhook triggers. Everything a running agent exposes lives
// here so the single-agent view doubles as an orchestration surface rather than
// an isolated transcript.
function AgentOrchestrationSection({
  agent,
}: {
  agent: AgentSession;
}) {
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  const [triggers, setTriggers] = useState<AgentTrigger[]>([]);
  const [state, setState] = useState<AgentState[]>([]);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<number | null>(null);
  const [viewing, setViewing] = useState<AgentArtifact | null>(null);
  const [expandedState, setExpandedState] = useState<string | null>(null);

  const loadOrch = useCallback(() => {
    void api.agents.listArtifacts(agent.id).then(setArtifacts).catch(() => setArtifacts([]));
    void api.agents.listTriggers(agent.id).then(setTriggers).catch(() => setTriggers([]));
    void api.agents.listState(agent.id).then(setState).catch(() => setState([]));
  }, [agent.id]);

  useEffect(() => {
    loadOrch();
    // Refresh live: mid-mission ARTIFACT directives publish to the blackboard as
    // they're emitted, and a completed turn publishes its result — the sidebar
    // must reflect those without waiting for a manual reload (mirrors the
    // in-chat AgentDeliverables refresh).
    const off = eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      if (ev.agent_session_id == null || ev.agent_session_id === agent.id) {
        loadOrch();
      }
    });
    return off;
  }, [loadOrch, agent.id]);

  // Auto-open an artifact from a `?artifact={id}` permalink once the list has
  // loaded, then strip the param so it doesn't re-fire on later reloads.
  useEffect(() => {
    if (artifacts.length === 0) return;
    const raw = new URLSearchParams(window.location.search).get("artifact");
    if (!raw) return;
    const target = artifacts.find((a) => String(a.id) === raw);
    if (target) setViewing(target);
    const url = new URL(window.location.href);
    url.searchParams.delete("artifact");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, [artifacts]);

  async function addWebhook(): Promise<void> {
    setBusy(true);
    try {
      const t = await api.agents.createTrigger(agent.id, { type: "webhook" });
      setTriggers((prev) => [...prev, t]);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  async function removeWebhook(triggerId: number): Promise<void> {
    setBusy(true);
    try {
      await api.agents.deleteTrigger(agent.id, triggerId);
      setTriggers((prev) => prev.filter((t) => t.id !== triggerId));
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  function copyHook(t: AgentTrigger): void {
    const url = `${window.location.origin}/api/agents/hooks/${t.token}`;
    void navigator.clipboard?.writeText(url).then(() => {
      setCopied(t.id);
      window.setTimeout(() => setCopied((c) => (c === t.id ? null : c)), 1500);
    });
  }

  async function removeStateKey(key: string): Promise<void> {
    setBusy(true);
    try {
      await api.agents.deleteState(agent.id, key);
      setState((prev) => prev.filter((s) => s.key !== key));
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  async function resetState(): Promise<void> {
    setBusy(true);
    try {
      await api.agents.clearState(agent.id);
      setState([]);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Shared artifacts (blackboard) */}
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <Package size={14} />
          <span>Artifacts</span>
          {artifacts.length > 0 && (
            <span className="text-[11px] text-muted">({artifacts.length})</span>
          )}
        </div>
        {artifacts.length === 0 ? (
          <p className="text-[11px] text-muted">
            Nothing published yet. Completed runs post their result here for downstream agents.
          </p>
        ) : (
          artifacts.slice(0, 8).map((a) => (
            <button
              type="button"
              key={a.id}
              onClick={() => setViewing(a)}
              className="block w-full rounded border border-border bg-surface/50 px-2 py-1 text-left transition hover:border-accent/50 hover:bg-surface"
              title={a.kind === "link" ? a.content : "Open artifact"}
            >
              <div className="flex items-center gap-1.5">
                {a.kind === "link" ? (
                  <ExternalLink size={11} className="shrink-0 text-accent" />
                ) : (
                  <Link2 size={11} className="shrink-0 text-muted" />
                )}
                <span className="min-w-0 flex-1 truncate text-[11px] font-medium">{a.title}</span>
                {a.key && (
                  <span className="shrink-0 rounded bg-border/60 px-1 text-[10px] text-muted">
                    {a.key}
                  </span>
                )}
              </div>
            </button>
          ))
        )}
      </div>

      {/* Durable state (private cross-run scratchpad) */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Database size={14} />
            <span>State</span>
            {state.length > 0 && <span className="text-[11px] text-muted">({state.length})</span>}
          </div>
          {state.length > 0 && (
            <button
              type="button"
              onClick={() => void resetState()}
              disabled={busy}
              className="rounded p-0.5 text-muted hover:bg-surface hover:text-red-500 disabled:opacity-50"
              data-tooltip="Reset saved state"
              aria-label="Reset saved state"
            >
              <RotateCcw size={14} />
            </button>
          )}
        </div>
        {state.length === 0 ? (
          <p className="text-[11px] text-muted">
            Nothing saved. Unlike artifacts, state survives re-runs — an agent stores its cursor
            here to resume where it left off.
          </p>
        ) : (
          state.map((s) => (
            <div key={s.key} className="rounded border border-border bg-surface/50">
              <div className="flex items-center gap-1.5 px-2 py-1">
                <button
                  type="button"
                  onClick={() => setExpandedState((k) => (k === s.key ? null : s.key))}
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  aria-expanded={expandedState === s.key}
                  title={expandedState === s.key ? "Hide value" : "Show value"}
                >
                  <ChevronRight
                    size={11}
                    className={`shrink-0 text-muted transition-transform ${
                      expandedState === s.key ? "rotate-90" : ""
                    }`}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-[10px]">{s.key}</span>
                  <span className="shrink-0 text-[10px] text-muted">{s.value.length}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void removeStateKey(s.key)}
                  disabled={busy}
                  className="rounded p-0.5 text-muted hover:text-red-500 disabled:opacity-50"
                  aria-label={`Delete state key ${s.key}`}
                >
                  <Trash2 size={12} />
                </button>
              </div>
              {expandedState === s.key && (
                <pre className="max-h-40 overflow-auto border-t border-border px-2 py-1 font-mono text-[10px] whitespace-pre-wrap break-words text-muted">
                  {s.value || "(empty)"}
                </pre>
              )}
            </div>
          ))
        )}
      </div>

      {/* External webhook triggers */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Webhook size={14} />
            <span>Webhooks</span>
          </div>
          <button
            type="button"
            onClick={() => void addWebhook()}
            disabled={busy}
            className="rounded p-0.5 text-muted hover:bg-surface disabled:opacity-50"
            data-tooltip="Create a webhook trigger"
            aria-label="Create a webhook trigger"
          >
            <Plus size={14} />
          </button>
        </div>
        {triggers.length === 0 ? (
          <p className="text-[11px] text-muted">
            No triggers. Add a webhook to re-run this agent from an external event.
          </p>
        ) : (
          triggers.map((t) => (
            <div
              key={t.id}
              className="flex items-center gap-1.5 rounded border border-border bg-surface/50 px-2 py-1"
            >
              <Webhook size={11} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted">
                …/hooks/{t.token.slice(0, 8)}
              </span>
              <button
                type="button"
                onClick={() => copyHook(t)}
                className="rounded p-0.5 text-muted hover:text-accent"
                data-tooltip={copied === t.id ? "Copied!" : "Copy URL"}
                aria-label="Copy webhook URL"
              >
                {copied === t.id ? <Check size={12} /> : <Copy size={12} />}
              </button>
              <button
                type="button"
                onClick={() => void removeWebhook(t.id)}
                disabled={busy}
                className="rounded p-0.5 text-muted hover:text-red-500 disabled:opacity-50"
                aria-label="Delete webhook"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))
        )}
      </div>

      {viewing && (
        <ArtifactViewer agent={agent} artifact={viewing} onClose={() => setViewing(null)} />
      )}
    </div>
  );
}

// A grouped tool call: input, output and any pending approval in one box.
interface ToolStep {
  key: string;
  toolName: string | null;
  input?: string;
  output?: string;
  done?: boolean;
  pending?: { data: Record<string, unknown>; requestId: string | null };
  /**
   * A permission request was raised for this call. The archived echo carries no
   * request id, so it's correlated positionally — it immediately follows the
   * tool start it gates. In a closed attempt this is what distinguishes "the
   * tool ran and we lost the result" from "it never ran: nobody answered".
   */
  gated?: boolean;
}

// A raised NEED_INPUT question, surfaced as a prominent amber callout inside the
// assistant bubble so the one line a human must act on never hides in prose.
function NeedInputCallout({
  question,
  onReply,
}: {
  question: string;
  onReply?: () => void;
}) {
  return (
    <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 dark:text-amber-200">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide">
        <HelpCircle size={13} className="shrink-0" />
        Needs your input
      </div>
      <Markdown className="mt-1 text-[12px] font-medium leading-relaxed text-text">
        {question}
      </Markdown>
      {onReply && (
        <button
          type="button"
          onClick={onReply}
          className="mt-2 inline-flex items-center gap-1 rounded-md bg-amber-500/20 px-2 py-1 text-[11px] font-medium text-amber-900 transition hover:bg-amber-500/30 dark:text-amber-100"
        >
          <CornerDownRight size={12} /> Answer
        </button>
      )}
    </div>
  );
}

// A milestone marker lifted onto the workflow spine: PROGRESS heartbeats are
// pulled out of the raw prose and rendered as compact, iconified timeline nodes
// so the mission's trajectory reads at a glance instead of hiding as plain-text
// directive lines. The terminal OBJECTIVE_COMPLETE is *not* a spine node — it's
// folded into the final answer bubble (see MessageNode) so the completion and
// the answer are one and the same.
function MissionMilestone({
  progress,
  at,
}: {
  progress?: { value: number; label: string | null } | null;
  at?: string | null;
}) {
  const when = formatTimestamp(at);
  if (progress) {
    const pct = Math.max(0, Math.min(100, Math.round(progress.value)));
    return (
      <div className="my-1 flex w-full justify-center">
        <div className="inline-flex max-w-xl items-center gap-2 rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1 text-[11px] text-violet-800 dark:text-violet-200">
          <Activity size={13} className="shrink-0" />
          <span className="font-semibold tabular-nums">{pct}%</span>
          {progress.label && (
            <span className="truncate text-violet-700/80 dark:text-violet-300/80">
              {progress.label}
            </span>
          )}
          {when && (
            <span className="shrink-0 text-violet-700/60 dark:text-violet-300/60">{when}</span>
          )}
        </div>
      </div>
    );
  }
  return null;
}

// A modal that renders one published artifact addressably: content by kind
// (markdown/json/text/link), plus Copy content, Copy permalink, and Open raw
// affordances so the output can be shared or consumed on its own. The permalink
// is `/agents/{ref}?artifact={id}`; the raw link hits the kind-typed raw API.
function ArtifactViewer({
  agent,
  artifact,
  onClose,
}: {
  agent: AgentSession;
  artifact: AgentArtifact;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState<"content" | "link" | null>(null);
  const rawUrl = api.agents.rawArtifactUrl(agent.id, artifact.id);
  const ref = agent.public_id ?? String(agent.id);
  const permalink = `${window.location.origin}/agents/${encodeURIComponent(
    ref,
  )}?artifact=${artifact.id}`;

  const flash = useCallback((which: "content" | "link") => {
    setCopied(which);
    window.setTimeout(() => setCopied((c) => (c === which ? null : c)), 1500);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Pretty-print JSON when the payload parses; otherwise show it verbatim.
  const pretty = useMemo(() => {
    if (artifact.kind !== "json") return artifact.content;
    try {
      return JSON.stringify(JSON.parse(artifact.content), null, 2);
    } catch {
      return artifact.content;
    }
  }, [artifact.kind, artifact.content]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Artifact: ${artifact.title}`}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-border bg-bg shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-2 border-b border-border px-4 py-3">
          <Package size={16} className="mt-0.5 shrink-0 text-teal-500" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-text">{artifact.title}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
              <span className="rounded bg-border/60 px-1 uppercase tracking-wide">
                {artifact.kind}
              </span>
              {artifact.key && (
                <span className="rounded bg-border/60 px-1 font-mono">{artifact.key}</span>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Close artifact"
          >
            <X size={16} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 py-3">
          {artifact.kind === "link" ? (
            <a
              href={artifact.content.trim()}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 break-all text-sm font-medium text-accent hover:underline"
            >
              <ExternalLink size={14} className="shrink-0" />
              {artifact.content.trim()}
            </a>
          ) : artifact.kind === "markdown" ? (
            <Markdown className="text-sm leading-relaxed text-text">{artifact.content}</Markdown>
          ) : (
            <pre className="whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed text-text">
              {pretty}
            </pre>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-2.5">
          {artifact.kind !== "link" && (
            <button
              type="button"
              onClick={() =>
                void navigator.clipboard?.writeText(artifact.content).then(() => flash("content"))
              }
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] font-medium text-muted hover:bg-surface hover:text-text"
            >
              {copied === "content" ? <Check size={13} /> : <Copy size={13} />}
              {copied === "content" ? "Copied" : "Copy content"}
            </button>
          )}
          <button
            type="button"
            onClick={() => void navigator.clipboard?.writeText(permalink).then(() => flash("link"))}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] font-medium text-muted hover:bg-surface hover:text-text"
          >
            {copied === "link" ? <Check size={13} /> : <Link2 size={13} />}
            {copied === "link" ? "Copied" : "Copy link"}
          </button>
          <a
            href={rawUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] font-medium text-muted hover:bg-surface hover:text-text"
          >
            <ExternalLink size={13} />
            Open raw
          </a>
        </div>
      </div>
    </div>
  );
}

// The `ARTIFACT:` directive's payload is no longer rendered inline in the message
// body: published outputs are surfaced once, at the foot of the turn, by
// `AgentDeliverables` (below) — the single, non-duplicated home for the answer.

// One deliverable, rendered unboxed into the discussion flow: a horizontal rule
// slips it off from the streamed prose, then the artifact body renders as plain
// Markdown (JSON in a fenced block, a `link` as a real anchor) so it reads as the
// agent's answer to the request — no card, no tinted background. A quiet title
// row labels it, and the copy/link/raw actions surface on hover.
function DeliverableAnswer({
  agent,
  artifact,
}: {
  agent: AgentSession;
  artifact: AgentArtifact;
}) {
  const [copied, setCopied] = useState<null | "content" | "link">(null);
  const rawUrl = api.agents.rawArtifactUrl(agent.id, artifact.id);
  const ref = agent.public_id ?? String(agent.id);
  const permalink = `${window.location.origin}/agents/${encodeURIComponent(
    ref,
  )}?artifact=${artifact.id}`;

  // Compose a Markdown document from the payload so every kind renders as prose:
  // JSON is fenced (pretty-printed), text/markdown pass through verbatim.
  const markdownBody = useMemo(() => {
    if (artifact.kind !== "json") return normalizeArtifactMarkdown(artifact.content);
    try {
      return `\`\`\`json\n${JSON.stringify(JSON.parse(artifact.content), null, 2)}\n\`\`\``;
    } catch {
      return `\`\`\`\n${artifact.content}\n\`\`\``;
    }
  }, [artifact.kind, artifact.content]);

  async function copy(kind: "content" | "link"): Promise<void> {
    try {
      await navigator.clipboard.writeText(kind === "link" ? permalink : artifact.content);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (insecure context); fail silently.
    }
  }

  return (
    <div className="group/deliv w-full max-w-xl">
      <hr className="mb-3 border-t border-border" />
      <div className="mb-1.5 flex items-center gap-1.5">
        <Package size={12} className="shrink-0 text-emerald-500/80" />
        <span className="min-w-0 flex-1 truncate text-[10px] font-medium uppercase tracking-wide text-muted">
          {artifact.title}
        </span>
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/deliv:opacity-100">
          {artifact.kind !== "link" && (
            <button
              type="button"
              onClick={() => void copy("content")}
              className="rounded p-1 text-muted hover:text-accent"
              aria-label="Copy content"
              data-tooltip="Copy content"
            >
              {copied === "content" ? (
                <Check size={12} className="text-emerald-500" />
              ) : (
                <Copy size={12} />
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => void copy("link")}
            className="rounded p-1 text-muted hover:text-accent"
            aria-label="Copy permalink"
            data-tooltip="Copy link"
          >
            {copied === "link" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Link2 size={12} />
            )}
          </button>
          <a
            href={rawUrl}
            target="_blank"
            rel="noreferrer"
            className="rounded p-1 text-muted hover:text-accent"
            aria-label="Open raw"
            data-tooltip="Open raw"
          >
            <ExternalLink size={12} />
          </a>
        </div>
      </div>

      {artifact.kind === "link" ? (
        <a
          href={artifact.content.trim()}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 break-all text-sm font-medium text-accent hover:underline"
        >
          <ExternalLink size={14} className="shrink-0" />
          {artifact.content.trim()}
        </a>
      ) : (
        <Markdown className="text-sm leading-relaxed text-text">{markdownBody}</Markdown>
      )}
    </div>
  );
}

// The agent's deliverables, surfaced inline at the foot of the transcript so the
// published outputs read as the agent's answer to the request — consumable right
// in the discussion, not only indexed in the insights sidebar. These are the
// *persisted* blackboard artifacts (stable id/kind), so each is the real,
// addressable deliverable. The sidebar list stays as a compact, agent-to-agent
// index.
function AgentDeliverables({ agent }: { agent: AgentSession }) {
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);

  useEffect(() => {
    let alive = true;
    const load = (): void =>
      void api.agents
        .listArtifacts(agent.id)
        .then((rows) => {
          if (alive) setArtifacts(rows);
        })
        .catch(() => {
          if (alive) setArtifacts([]);
        });
    load();
    // Refresh on agent.changed: a completed turn publishes its result and
    // mid-mission ARTIFACT directives publish as they're emitted.
    const off = eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      if (ev.agent_session_id == null || ev.agent_session_id === agent.id) load();
    });
    return () => {
      alive = false;
      off();
    };
  }, [agent.id]);

  // A single, non-duplicated answer to the request. Prefer the model's explicit
  // `ARTIFACT:` outputs (provenance `key !== "result"`) — those are the real
  // deliverable. Fall back to the auto-captured completion summary (`key ===
  // "result"`) only when nothing explicit was published, because that summary is
  // otherwise already shown on the Objective-complete milestone. This stops the
  // same content repeating as prose, milestone, and deliverable.
  const outputs = artifacts.filter((a) => a.key !== "result");
  const shown = outputs.length > 0 ? outputs : artifacts.filter((a) => a.key === "result");
  if (shown.length === 0) return null;

  return (
    <div className="flex w-full flex-col items-start gap-3">
      {shown.map((a) => (
        <DeliverableAnswer key={a.id} agent={agent} artifact={a} />
      ))}
    </div>
  );
}

// One simple message node (user/system/assistant/reasoning/error).
function MessageNode({
  event,
  category,
  isLastAnswer,
  autonomy,
  user,
  model,
  elapsedMs,
  onPickSuggestion,
  onReply,
  suggestionsDisabled,
}: {
  event: AgentEvent;
  category: "user" | "system" | "assistant" | "reasoning" | "error";
  isLastAnswer: boolean;
  // Whether the parent agent is autonomy-enabled — gates directive parsing so a
  // normal agent that happens to type "PROGRESS:" isn't rewritten.
  autonomy?: boolean;
  user?: { name: string; avatarUrl: string | null };
  // Session model + computed generation time, shown on assistant answers only.
  model?: string | null;
  elapsedMs?: number | null;
  onPickSuggestion?: (text: string) => void;
  // Focus the reply composer — offered on a raised NEED_INPUT question.
  onReply?: () => void;
  suggestionsDisabled?: boolean;
}) {
  const style = CATEGORY_STYLE[category];
  const box = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/15 ring-1 ring-emerald-500/30"
    : style.box;
  const marker = isLastAnswer
    ? "border-emerald-500/50 bg-emerald-500/20 text-emerald-600 dark:text-emerald-300"
    : style.marker;
  // For the user's own prompt, show their GitHub persona (avatar + name) rather
  // than a generic icon/label.
  const isUser = category === "user";
  const isSystem = category === "system";
  const isAssistant = category === "assistant";
  const label = isUser && user ? user.name : style.label;
  // Assistant turns may end with a `suggest` block; hide it from the rendered
  // text and surface it as chips under the final answer instead. Autonomy
  // agents additionally embed control directives (PROGRESS/ARTIFACT/…) — strip
  // those lines so the body reads as prose and the raised NEED_INPUT question
  // stands out as a callout rather than a buried plain-text line.
  const directives = isAssistant && autonomy ? parseAgentDirectives(event.text ?? "") : null;
  const rawAssistant = isAssistant ? stripSuggestionBlock(event.text ?? "") : (event.text ?? "");
  const assistantText = autonomy && isAssistant ? stripAgentDirectives(rawAssistant) : rawAssistant;
  // A terminal OBJECTIVE_COMPLETE turn *is* the answer, so fold the completion
  // into this bubble — a completion badge, and (when the prose was entirely
  // directives) the summary as the body — instead of repeating it as a separate
  // spine milestone below an otherwise-hollow answer bubble.
  const completionSummary = isAssistant && autonomy ? (directives?.complete ?? null) : null;
  const isCompletion = completionSummary != null;
  const bodyText = assistantText || (completionSummary ?? "");
  const suggestions =
    isAssistant && isLastAnswer && onPickSuggestion
      ? parseSuggestions(event.text ?? "")
      : [];
  // The system message (the long base prompt) is collapsed to a few lines with
  // a Details toggle, like a tool box, so it never floods the timeline.
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState<null | "text" | "md">(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // Copy the rendered text (markdown stripped) or the raw markdown source, the
  // same pair of actions offered on topic/chat assistant bubbles.
  const copyTo = async (kind: "text" | "md") => {
    const value =
      kind === "md"
        ? (isAssistant ? bodyText : (event.text ?? ""))
        : (contentRef.current?.textContent ?? event.text ?? "").trim();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 1200);
    } catch {
      // Clipboard may be unavailable (e.g. insecure context); fail silently.
    }
  };
  return (
    <div
      className={`group/node relative w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${box}`}
    >
      <div className="flex items-center gap-2">
        {isUser && user?.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.name}
            className="h-6 w-6 shrink-0 rounded-full border border-sky-500/40 object-cover"
          />
        ) : (
          <span
            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}
          >
            {style.icon}
          </span>
        )}
        <span className={`text-[11px] font-semibold ${isUser && user ? "" : "capitalize"}`}>
          {label}
        </span>
        {isCompletion ? (
          <span className="inline-flex items-center gap-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 size={10} className="shrink-0" />
            Objective complete
          </span>
        ) : isLastAnswer ? (
          <span className="rounded bg-emerald-500/20 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
            Answer
          </span>
        ) : null}
      </div>
      {event.text &&
        (isSystem ? (
          <div className="mt-1">
            <p
              className={`whitespace-pre-wrap text-[11px] text-muted ${open ? "" : "line-clamp-3"}`}
            >
              {event.text}
            </p>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="mt-1 flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
              data-tooltip={open ? "Collapse" : "Show full system message"}
            >
              <ChevronDown
                size={12}
                className={`transition-transform ${open ? "rotate-180" : ""}`}
              />
              {open ? "Hide" : "Details"}
            </button>
          </div>
        ) : isAssistant ? (
          <div ref={contentRef}>
            {bodyText && (
              <Markdown className="mt-1 text-[11px] leading-relaxed text-muted">
                {bodyText}
              </Markdown>
            )}
            {directives?.needInput && (
              <NeedInputCallout question={directives.needInput} onReply={onReply} />
            )}
          </div>
        ) : (
          <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
            <HighlightedText
              text={
                event.text.length > 1500
                  ? `${event.text.slice(0, 1500)}…`
                  : event.text
              }
            />
          </p>
        ))}
      {isAssistant && event.text && (
        <div className="absolute -bottom-3 right-2 z-10 flex items-center gap-1 rounded-full border border-border bg-surface px-1 py-0.5 opacity-0 shadow-sm transition-opacity group-hover/node:opacity-100">
          <button
            type="button"
            onClick={() => copyTo("text")}
            className="rounded-full p-1 text-muted hover:text-accent"
            aria-label="Copy message"
            data-tooltip="Copy message"
          >
            {copied === "text" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Copy size={12} />
            )}
          </button>
          <button
            type="button"
            onClick={() => copyTo("md")}
            className="rounded-full p-1 text-muted hover:text-accent"
            aria-label="Copy raw markdown"
            data-tooltip="Copy raw markdown"
          >
            {copied === "md" ? (
              <Check size={12} className="text-emerald-500" />
            ) : (
              <Code2 size={12} />
            )}
          </button>
        </div>
      )}
      {suggestions.length > 0 && onPickSuggestion && (
        <SuggestedReplies
          items={suggestions}
          onPick={onPickSuggestion}
          disabled={suggestionsDisabled}
          className="mt-2"
        />
      )}
      <div className="mt-1.5">
        <MessageMeta
          createdAt={event.at}
          model={isAssistant ? model : null}
          elapsedMs={isAssistant ? elapsedMs : null}
          hoverGroup="node"
        />
      </div>
    </div>
  );
}

// Optimistic echo of a just-sent prompt. Rendered immediately on send — before
// the backend has recorded the real user turn — so it's obvious the message
// left the composer even while the session spins the turn up. Styled like the
// real user node but muted, with a "Sending…" spinner; it's swapped out for the
// real event the moment it lands.
function PendingUserBubble({
  text,
  user,
}: {
  text: string;
  user?: { name: string; avatarUrl: string | null };
}) {
  const style = CATEGORY_STYLE.user;
  return (
    <div
      className={`relative w-full max-w-xl rounded-lg border p-2.5 opacity-70 ${style.box}`}
      aria-busy="true"
    >
      <div className="flex items-center gap-2">
        {user?.avatarUrl ? (
          <img
            src={user.avatarUrl}
            alt={user.name}
            className="h-6 w-6 shrink-0 rounded-full border border-sky-500/40 object-cover"
          />
        ) : (
          <span
            className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${style.marker}`}
          >
            {style.icon}
          </span>
        )}
        <span className="text-[11px] font-semibold">{user?.name ?? "You"}</span>
        <span className="ml-auto flex items-center gap-1 text-[10px] text-muted">
          <Loader2 size={11} className="animate-spin" />
          Sending…
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[11px] text-muted">
        {text.length > 1500 ? `${text.slice(0, 1500)}…` : text}
      </p>
    </div>
  );
}

// A grouped tool call. Shows the tool name + status; input/output collapse until
// the user wants to see "what was done"; a pending approval renders inline.
function ToolBox({
  step,
  busy,
  onDecision,
  closed = false,
}: {
  step: ToolStep;
  busy: boolean;
  onDecision: DecisionHandler;
  /**
   * The event stream this box belongs to has ended (a finished workflow step
   * attempt). Nothing can still be running in it, so an unterminated call is
   * reported for what it is rather than left spinning forever.
   */
  closed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const detailOpen = open;
  const hasDetail = Boolean(step.input || step.output);
  const unfinished = !step.done && !step.pending;
  const status = step.pending
    ? "awaiting approval"
    : step.done
      ? "done"
      : closed
        ? // A closed attempt that stopped at a gate never ran the call at all;
          // one that stopped elsewhere was cut off mid-flight.
          step.gated
          ? "never approved — attempt ended"
          : "interrupted — attempt ended"
        : "running";
  const style = CATEGORY_STYLE.tool;
  const stale = closed && unfinished;
  const box = step.pending || stale ? CATEGORY_STYLE.permission.box : style.box;
  const marker = step.pending || stale ? CATEGORY_STYLE.permission.marker : style.marker;

  return (
    <div
      className={`w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${box}`}
    >
      <div className="flex items-center gap-2">
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${marker}`}>
          {status === "running" ? (
            <Loader2 size={13} className="animate-spin" />
          ) : step.pending || stale ? (
            <ShieldQuestion size={13} />
          ) : (
            toolIcon(step.toolName)
          )}
        </span>
        <span className="truncate text-[11px] font-semibold">{step.toolName || "Tool"}</span>
        <span className={`text-[10px] ${stale ? "text-orange-500" : "text-muted"}`}>{status}</span>
        {hasDetail && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
            data-tooltip={detailOpen ? "Hide details" : "Show what was done"}
          >
            <ChevronDown
              size={12}
              className={`transition-transform ${detailOpen ? "rotate-180" : ""}`}
            />
            {detailOpen ? "Hide" : "Details"}
          </button>
        )}
      </div>

      {detailOpen && hasDetail && (
        <div className="mt-1.5 space-y-1.5">
          {step.input && <ToolField label="input" value={step.input} />}
          {step.output && <ToolField label="output" value={step.output} />}
        </div>
      )}

      {step.pending && (
        <PermissionBody
          data={step.pending.data}
          requestId={step.pending.requestId}
          busy={busy}
          onDecision={onDecision}
        />
      )}
    </div>
  );
}

function ToolField({ label, value }: { label: string; value: string }) {
  const [expanded, setExpanded] = useState(false);
  // Collapsed keeps the box compact (a short scrollable window) and hard-caps
  // very long blobs so they can't freeze layout — but that also means content
  // past the cap can't be reached by scrolling. Expanding lifts both limits and
  // wraps long lines so the whole input/output is visible on demand.
  const LIMIT = 2000;
  const clipped = value.length > LIMIT || value.split("\n").length > 12;
  const shown = expanded || value.length <= LIMIT ? value : `${value.slice(0, LIMIT)}…`;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[9px] font-medium uppercase tracking-wide text-muted">{label}</div>
        {clipped && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted hover:bg-bg"
            data-tooltip={expanded ? "Collapse" : "Show full content"}
          >
            <ChevronDown
              size={11}
              className={`transition-transform ${expanded ? "rotate-180" : ""}`}
            />
            {expanded ? "Collapse" : "Expand"}
          </button>
        )}
      </div>
      <pre
        className={`mt-0.5 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug ${
          expanded ? "whitespace-pre-wrap break-words" : "max-h-48"
        }`}
      >
        {shown}
      </pre>
    </div>
  );
}

// A searchable topic lookup (combobox), used to associate an agent with a topic.
// Extracted to ./TopicPicker so the Live meeting session picker can reuse it;
// re-exported here to keep existing import sites (AgentSettingsPanel) working.
export { TopicPicker };

// Persisted "Show" toggle preferences for the workflow timeline. One JSON blob
// in localStorage so a user's choices stick across agents and app restarts.
type ShowPrefs = {
  system: boolean;
  assistant: boolean;
  thinking: boolean;
  tool: boolean;
  lifecycle: boolean;
};

const SHOW_PREFS_KEY = "precursor:agent-show-prefs";

const DEFAULT_SHOW_PREFS: ShowPrefs = {
  system: false,
  assistant: true,
  thinking: true,
  tool: true,
  lifecycle: true,
};

function readShowPrefs(): ShowPrefs {
  if (typeof window === "undefined") return DEFAULT_SHOW_PREFS;
  try {
    const raw = window.localStorage.getItem(SHOW_PREFS_KEY);
    if (raw) return { ...DEFAULT_SHOW_PREFS, ...(JSON.parse(raw) as Partial<ShowPrefs>) };
  } catch {
    /* corrupt/unavailable storage — fall back to defaults */
  }
  return DEFAULT_SHOW_PREFS;
}

// A row in the rendered workflow: a centered message node, a grouped tool box,
// or a side hook bubble.
type WorkflowRow =
  | { type: "node"; ev: AgentEvent; cat: "user" | "system" | "assistant" | "reasoning" | "error" }
  | { type: "tool"; step: ToolStep }
  | { type: "hook"; ev: AgentEvent };

// Collapse the raw event stream into renderable rows. Tool start/output and any
// pending approval that share a request_id are merged into one ToolStep box so
// the workflow reads as discrete actions rather than scattered events.
function buildRows(events: AgentEvent[]): WorkflowRow[] {
  const rows: WorkflowRow[] = [];
  const toolIndex = new Map<string, ToolStep>();
  // A tool box is placed in the flow at its *start*, not at whatever event for
  // it arrives first. Interrupted/resumed turns can stream a tool's completion
  // before its start (out of order); without this, the box floats above the
  // assistant message that requested it. Steps seen completion-first are held
  // here until their start/partial arrives, then appended at the end as a
  // fallback if a start never comes.
  const placed = new Set<ToolStep>();
  const unplaced: ToolStep[] = [];
  // The last tool call that hasn't terminated — what a bare permission echo
  // refers to. The archived `PermissionRequestedData` carries no request id, so
  // this positional link is the only way to know a call stopped at a gate.
  let openTool: ToolStep | null = null;

  const pick = (data: Record<string, unknown> | null, ...keys: string[]): string | undefined => {
    if (!data) return undefined;
    for (const k of keys) {
      const v = data[k];
      if (typeof v === "string" && v.trim()) return v;
    }
    return undefined;
  };

  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    const cat = classify(ev);
    if (cat === "skip") continue;
    // Permission echoes carry no actionable content of their own, but a
    // *request* marks the call before it as gated — the difference between a
    // tool that ran and one that only ever asked.
    if (kind.includes("permissionrequested")) {
      if (openTool) openTool.gated = true;
      continue;
    }
    if (kind.includes("permissioncompleted")) continue;

    const isToolish = cat === "tool" || cat === "permission";
    if (isToolish) {
      const groupKey = ev.request_id ?? `tool-${rows.length}`;
      let step = ev.request_id ? toolIndex.get(groupKey) : undefined;
      // A tool is "done" when its completion event arrives — independent of
      // whether it carried any output text (many tools complete silently).
      // Partial-result events stream interim output but don't finish the tool.
      const isComplete = kind.includes("complete");
      const isFinalResult = kind.includes("result") && !kind.includes("partial");
      const isTerminal = isComplete || isFinalResult;
      if (!step) {
        step = { key: groupKey, toolName: ev.tool_name };
        if (ev.request_id) toolIndex.set(groupKey, step);
        if (isTerminal) {
          // Completion before start → defer placement until the start arrives.
          unplaced.push(step);
        } else {
          rows.push({ type: "tool", step });
          placed.add(step);
        }
      } else if (!placed.has(step) && !isTerminal) {
        // The (late) start/partial for a deferred step — place it in order now.
        rows.push({ type: "tool", step });
        placed.add(step);
      }
      if (ev.tool_name) step.toolName = ev.tool_name;
      const input = pick(ev.data, "arguments", "input");
      if (input) step.input = input;
      if (isComplete || isFinalResult) {
        const output = pick(ev.data, "result", "output") ?? ev.text ?? undefined;
        if (output) step.output = output;
      }
      if (isComplete || isFinalResult) step.done = true;
      if (kind === "permission_request") {
        step.pending = { data: (ev.data ?? {}) as Record<string, unknown>, requestId: ev.request_id };
      }
      // Track what a following bare permission echo would refer to.
      openTool = step.done ? null : step;
      continue;
    }

    if (cat === "hook") {
      rows.push({ type: "hook", ev });
    } else if (cat === "assistant" && (!ev.text || !ev.text.trim())) {
      // Streaming emits an empty AssistantMessageStartData marker per message;
      // drop the contentless frames so they don't render as blank "Assistant"
      // steps. The real answer arrives as AssistantMessageData (with text).
      continue;
    } else if (cat === "reasoning") {
      // Streamed reasoning deltas carry no standalone text (the SDK sends the
      // full block separately as AssistantReasoningData); drop the empty ones so
      // streaming mode doesn't fill the timeline with blank "thinking" steps.
      if (!ev.text || !ev.text.trim()) continue;
      // The SDK emits reasoning right AFTER the assistant message of a turn;
      // surface it just before so each turn reads think → speak and a trailing
      // reasoning never dangles below the final answer.
      let at = rows.length;
      while (at > 0 && rows[at - 1].type === "hook") at--;
      const anchor = at > 0 ? rows[at - 1] : undefined;
      if (anchor && anchor.type === "node" && anchor.cat === "assistant") {
        rows.splice(at - 1, 0, { type: "node", ev, cat });
      } else {
        rows.push({ type: "node", ev, cat });
      }
    } else {
      rows.push({ type: "node", ev, cat });
    }
  }
  // Fallback: tools whose start never arrived (truly broken turn) still render,
  // appended in creation order rather than vanishing.
  for (const step of unplaced) {
    if (!placed.has(step)) {
      rows.push({ type: "tool", step });
      placed.add(step);
    }
  }
  return rows;
}

// Derive a per-assistant-answer "generation time" (ms) from event timestamps,
// mirroring the elapsed readout on topic/chat turns. A turn is anchored at its
// `turn_start` (or the user prompt, when the SDK omits the marker); each
// assistant message in that turn is measured from that anchor. Keyed by the
// event object so the timeline can look it up while rendering.
function computeElapsedByEvent(events: AgentEvent[]): Map<AgentEvent, number> {
  const map = new Map<AgentEvent, number>();
  let anchorAt: number | null = null;
  const parse = (at: string | null): number | null => {
    if (!at) return null;
    const t = new Date(at).getTime();
    return Number.isNaN(t) ? null : t;
  };
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    const atMs = parse(ev.at);
    if (kind === "turn_start" || classify(ev) === "user") {
      if (atMs != null) anchorAt = atMs;
      continue;
    }
    if (
      classify(ev) === "assistant" &&
      ev.text &&
      ev.text.trim() &&
      atMs != null &&
      anchorAt != null
    ) {
      const delta = atMs - anchorAt;
      if (delta >= 0) map.set(ev, delta);
    }
  }
  return map;
}

// Resolve the concrete model for each assistant answer from its turn's usage
// event. The SDK emits an `AssistantUsageData` (kind "usage") carrying the
// resolved model just before each assistant message in the same turn, so we
// track the latest usage model seen since the turn started and tag each answer
// with it. The call site falls back to the session model when a turn predates
// this capture (older archived runs) or reported no usage.
function computeModelByEvent(events: AgentEvent[]): Map<AgentEvent, string> {
  const map = new Map<AgentEvent, string>();
  let currentModel: string | null = null;
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    if (kind === "turn_start") {
      currentModel = null;
      continue;
    }
    if (kind === "usage") {
      const m = ev.data?.model;
      if (typeof m === "string" && m.trim()) currentModel = m;
      continue;
    }
    if (classify(ev) === "assistant" && ev.text && ev.text.trim() && currentModel) {
      map.set(ev, currentModel);
    }
  }
  return map;
}

// out of the DOM until the user scrolls toward the top, where another window's
// worth is revealed. Keeps very long agent runs from rendering thousands of
// nodes up front.
const AGENT_SEGMENT_WINDOW = 40;

/**
 * The agent workflow timeline, read-only.
 *
 * The same rows the Agents cockpit renders — tool boxes with their arguments and
 * output, reasoning, assistant messages, lifecycle hooks — built by the same
 * ``buildRows``, so a workflow step's activity is described exactly as a
 * standalone agent's is rather than by a lookalike that drifts from it. What's
 * dropped is only what needs a live session: approving a parked permission,
 * suggestion chips, and the reply affordance.
 */
export function AgentActivity({
  events,
  model = null,
  autonomy = false,
  closed = false,
}: {
  events: AgentEvent[];
  /** Session model, used when a turn reported no usage event of its own. */
  model?: string | null;
  /** Gates directive parsing, matching the cockpit's behaviour. */
  autonomy?: boolean;
  /**
   * This stream has ended — a finished workflow step attempt. Tool calls left
   * unterminated by it are reported as interrupted (or never approved) rather
   * than spinning as though they were still going.
   */
  closed?: boolean;
}) {
  const rows = useMemo(() => buildRows(events), [events]);
  const elapsedByEvent = useMemo(() => computeElapsedByEvent(events), [events]);
  const modelByEvent = useMemo(() => computeModelByEvent(events), [events]);

  // Segment exactly as the cockpit does: lifecycle hooks accumulate and are
  // attached to the connector *before* the next real row, so they read as side
  // information between steps rather than as steps themselves.
  const { segments, trailingHooks } = useMemo(() => {
    const segs: { row: WorkflowRow; hooks: AgentEvent[] }[] = [];
    let pendingHooks: AgentEvent[] = [];
    for (const r of rows) {
      if (r.type === "hook") {
        pendingHooks.push(r.ev);
        continue;
      }
      segs.push({ row: r, hooks: pendingHooks });
      pendingHooks = [];
    }
    return { segments: segs, trailingHooks: pendingHooks };
  }, [rows]);

  if (segments.length === 0 && trailingHooks.length === 0) {
    return (
      <p className="py-2 text-center text-[11px] text-muted">
        No activity was recorded for this attempt.
      </p>
    );
  }

  return (
    // Same width the cockpit gives its timeline. The spine is centred on this
    // column, so letting it stretch to the full trace width would fling the
    // left- and right-aligned bubbles away from the arrows that connect them.
    <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col items-center">
      {segments.map((seg, idx) => {
        const align =
          seg.row.type === "node" && seg.row.cat === "user"
            ? "justify-end"
            : seg.row.type === "node" && seg.row.cat === "assistant"
              ? "justify-start"
              : "justify-center";
        return (
          <Fragment key={idx}>
            {idx === 0 ? (
              seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} />
            ) : (
              <StepConnector hooks={seg.hooks} />
            )}
            <div className={`flex w-full ${align}`}>
              {seg.row.type === "tool" ? (
                // Read-only: a historical attempt has no live session to decide
                // a permission against, so the box renders its detail only.
                <ToolBox step={seg.row.step} busy onDecision={() => {}} closed={closed} />
              ) : seg.row.type === "node" ? (
                <MessageNode
                  event={seg.row.ev}
                  category={seg.row.cat}
                  isLastAnswer={false}
                  autonomy={autonomy}
                  model={modelByEvent.get(seg.row.ev) ?? model}
                  elapsedMs={elapsedByEvent.get(seg.row.ev) ?? null}
                  suggestionsDisabled
                />
              ) : null}
            </div>
          </Fragment>
        );
      })}
      {trailingHooks.length > 0 && <HookGutter hooks={trailingHooks} />}
    </div>
  );
}

export function AgentView({
  agents,
  agentId,
  enabled,
  available,
  unavailableReason,
  onReload,
  onSelect,
  onOpenSettings,
  draftTopicId,
}: AgentViewProps) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  // Narrows the transcript to a single execution. A reusable agent driven by two
  // workflows at once produces two conversations in one archive; reading them
  // interleaved is meaningless (issue #242).
  //
  // This holds the user's *choice*, not the filter: `null` means "follow the
  // newest run", which is the default so opening an agent shows what it just
  // did rather than every execution ever stitched together. Picking a run or
  // "All runs" pins the view until the user switches agent.
  const [runChoice, setRunChoice] = useState<number | "all" | null>(null);
  // The agent's executions. Loaded here rather than inside the Runs rail because
  // the transcript defaults to the newest one — a collapsed insights panel must
  // not change what you read. Tagged with the agent they belong to so switching
  // agents can't briefly scope the transcript to the previous one's run.
  const [runsState, setRunsState] = useState<{ agentId: number | null; items: AgentRun[] }>({
    agentId: null,
    items: [],
  });
  const [topics, setTopics] = useState<Topic[]>([]);
  const [collections, setCollections] = useState<Collection[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [task, setTask] = useState("");
  const [newTopicId, setNewTopicId] = useState<number | null>(null);
  // Autonomy opt-in for the next started agent: when on, it runs a goal loop
  // toward the objective and pauses only by exception (default off).
  const [newAutonomy, setNewAutonomy] = useState(false);
  const [newMaxSteps, setNewMaxSteps] = useState(12);
  // Per-agent approval-policy override for the next started agent. "" (empty)
  // means inherit the global default set in Settings.
  const [newApprovalPolicy, setNewApprovalPolicy] = useState<AgentApprovalPolicy | "">("");
  // Whether the composer launches the agent immediately (default) or parks it in
  // the `waiting` state, armed for a later trigger (parent completion, webhook,
  // or a manual "Start now").
  const [newStart, setNewStart] = useState(true);
  const [followUp, setFollowUp] = useState("");
  // Wrapper around the reply composer, so a raised NEED_INPUT question can jump
  // the human straight to the answer box (scroll into view + focus).
  const composerWrapRef = useRef<HTMLDivElement>(null);
  const focusComposer = useCallback(() => {
    const wrap = composerWrapRef.current;
    if (!wrap) return;
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    wrap.querySelector("textarea")?.focus();
  }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Optimistic echo of a just-sent prompt, scoped to the agent it targets so it
  // survives the switch into a freshly created session but never leaks onto
  // another. Cleared once the backend records the real user turn.
  const [pending, setPending] = useState<{ agentId: number; text: string } | null>(null);

  // Preselect the originating topic in the new-agent form when opened via
  // "/agent" from a topic (only while no session is selected).
  useEffect(() => {
    if (agentId == null && draftTopicId != null) setNewTopicId(draftTopicId);
  }, [agentId, draftTopicId]);

  // Workflow display toggles, persisted across sessions (localStorage). System
  // (the big base prompt) is noise by default. "Tool" shows/hides whole tool
  // boxes (their I/O stays collapsed, expandable per-box). "Thinking" shows/hides
  // the agent's reasoning steps.
  const [showPrefs, setShowPrefs] = useState<ShowPrefs>(readShowPrefs);
  useEffect(() => {
    try {
      window.localStorage.setItem(SHOW_PREFS_KEY, JSON.stringify(showPrefs));
    } catch {
      /* private mode / quota — prefs just won't persist */
    }
  }, [showPrefs]);
  const toggleShow = (k: keyof ShowPrefs) => setShowPrefs((p) => ({ ...p, [k]: !p[k] }));

  // Shared composer infrastructure (same as topics/chats): resizable height,
  // dictation, ↑/↓ history, and a slash/skills picker. Only one composer is
  // mounted at a time (the start form *or* the follow-up box), so a single
  // speech/skills setup serves both — dictation targets whichever is active.
  const settings = useSettings();
  // Short label of the global approval default, shown on the per-agent
  // "Inherit" option so the fallback is legible without opening Settings.
  const globalPolicyLabel = useMemo(() => {
    const found = APPROVAL_POLICIES.find((p) => p.value === settings?.agents_approval_policy);
    return found ? found.label.split("—")[0].trim() : "";
  }, [settings?.agents_approval_policy]);
  const selectedRef = useRef(false);
  const { height: composerHeight, onMouseDown: onComposerResize } = useResizableHeight({
    storageKey: "precursor:agent-composer:height",
    defaultHeight: 56,
    min: 40,
    max: 480,
  });
  const [interimText, setInterimText] = useState("");
  const appendFinalChunk = (text: string) => {
    const chunk = text.trim();
    if (!chunk) return;
    const append = (d: string) => (d ? `${d.replace(/\s+$/, "")} ${chunk}` : chunk);
    if (selectedRef.current) setFollowUp(append);
    else setTask(append);
    setInterimText("");
  };
  const speech = useAzureSpeech({
    onFinalChunk: appendFinalChunk,
    onInterim: setInterimText,
    enabled: settings?.stt_azure_ready ?? false,
    lang: settings?.azure_speech_language || undefined,
  });
  useEffect(() => {
    if (!speech.listening) setInterimText("");
  }, [speech.listening]);

  // Agents support only the system-handled slash commands (/rename, /clear,
  // /archive); skills and every other builtin are disabled here. They only apply
  // to an existing session, so the start composer offers none and the follow-up
  // composer offers just those three. The backend rejects anything else.
  const taskSuggestions = useMemo<SlashCommand[]>(() => [], []);
  const followUpSuggestions = useMemo<SlashCommand[]>(
    () => matchAgentSlashCommands(followUp) ?? [],
    [followUp],
  );

  // Autoscroll: keep the newest step in view as the workflow grows, but only
  // while the user is parked at the bottom (don't yank them away mid-scroll).
  const scrollRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  // Reverse-windowing of the rendered timeline: only segments from
  // `windowStart` onward are mounted. The start is a stable absolute index so
  // appending new segments never shifts what's already on screen; scrolling
  // toward the top lowers it to reveal older history.
  const [windowStart, setWindowStart] = useState(0);
  const windowStartRef = useRef(0);
  // Captured scrollHeight from just before revealing older segments, so the
  // layout effect can keep the viewport anchored after they mount.
  const revealAnchorRef = useRef<number | null>(null);
  useEffect(() => {
    windowStartRef.current = windowStart;
  }, [windowStart]);

  // Keep the viewport steady when older segments are revealed at the top.
  useLayoutEffect(() => {
    if (revealAnchorRef.current == null) return;
    const box = scrollRef.current;
    if (box) box.scrollTop += box.scrollHeight - revealAnchorRef.current;
    revealAnchorRef.current = null;
  }, [windowStart]);

  const selected = useMemo(
    () => agents.find((a) => a.id === agentId) ?? null,
    [agents, agentId],
  );
  useEffect(() => {
    selectedRef.current = selected != null;
  }, [selected]);

  // Derive the workflow timeline (segments + trailing hooks + which assistant
  // rows are the real "answers") from the raw events. Memoised so windowing
  // effects can react to the segment count without rebuilding on every render.
  const selectedStatus = selected?.status ?? null;
  const timeline = useMemo(() => {
    const rows = buildRows(events);
    const terminal = ["idle", "completed", "interrupted", "failed", "cancelled"].includes(
      selectedStatus ?? "",
    );
    const answerRows = new Set<WorkflowRow>();
    let lastAssistant: WorkflowRow | null = null;
    for (const r of rows) {
      if (r.type === "node" && r.cat === "assistant") {
        lastAssistant = r;
      } else if (r.type === "hook" && r.ev.kind.toLowerCase().includes("idle")) {
        if (lastAssistant) answerRows.add(lastAssistant);
        lastAssistant = null;
      }
    }
    if (terminal && lastAssistant) answerRows.add(lastAssistant);

    const visible = rows.filter((r) => {
      if (r.type === "hook") return showPrefs.lifecycle;
      if (r.type === "tool") return showPrefs.tool;
      if (r.type !== "node") return true;
      if (r.cat === "system" && !showPrefs.system) return false;
      if (r.cat === "reasoning" && !showPrefs.thinking) return false;
      // Keep answers visible even when assistant chatter is hidden.
      if (r.cat === "assistant" && !showPrefs.assistant && !answerRows.has(r)) return false;
      return true;
    });

    const segments: { row: WorkflowRow; hooks: AgentEvent[] }[] = [];
    let pendingHooks: AgentEvent[] = [];
    for (const r of visible) {
      if (r.type === "hook") {
        pendingHooks.push(r.ev);
        continue;
      }
      segments.push({ row: r, hooks: pendingHooks });
      pendingHooks = [];
    }
    return { segments, trailingHooks: pendingHooks, answerRows };
  }, [events, showPrefs, selectedStatus]);

  // Per-assistant-answer generation time, derived from event timestamps and
  // looked up by event object while rendering each node's metadata line.
  const elapsedByEvent = useMemo(() => computeElapsedByEvent(events), [events]);

  // Per-assistant-answer resolved model (from the turn's usage event), with the
  // session model as fallback at the render site.
  const modelByEvent = useMemo(() => computeModelByEvent(events), [events]);

  // Whether the backend has now recorded the real user turn for our optimistic
  // echo. Computed in render so the echo is dropped in the same paint that shows
  // the real node — no duplicate flashes — while the effect clears the state.
  const pendingLanded = useMemo(() => {
    if (!pending) return false;
    for (let i = events.length - 1; i >= 0; i--) {
      if (classify(events[i]) !== "user") continue;
      return (events[i].text ?? "").trim() === pending.text.trim();
    }
    return false;
  }, [events, pending]);
  useEffect(() => {
    if (pendingLanded) setPending(null);
  }, [pendingLanded]);
  const showPending = pending != null && pending.agentId === agentId && !pendingLanded;

  // Maintain the render window as the timeline grows. While the user is parked
  // at the bottom, keep it bounded to the most recent page so long runs don't
  // accumulate unbounded DOM. While scrolled up, leave the start fixed (only
  // clamped) so newly appended steps never shift the history being read.
  const segmentCount = timeline.segments.length;
  useEffect(() => {
    setWindowStart((start) =>
      pinnedRef.current
        ? Math.max(0, segmentCount - AGENT_SEGMENT_WINDOW)
        : Math.min(start, Math.max(0, segmentCount - 1)),
    );
  }, [segmentCount]);

  const loadEvents = useCallback(
    async (id: number, agentRunId: number | null = null): Promise<void> => {
      try {
        setEvents(await api.agents.getEvents(id, agentRunId));
      } catch {
        setEvents([]);
      }
    },
    [],
  );

  useEffect(() => {
    if (!enabled) return;
    void api.topics.list().then(setTopics).catch(() => setTopics([]));
    void api.collections.list().then(setCollections).catch(() => setCollections([]));
    void api.me.get().then(setMe).catch(() => setMe(null));
  }, [enabled]);

  // The user's GitHub persona (name + avatar) for the user-prompt node, with a
  // sensible fallback when not signed in.
  const userPersona = useMemo(
    () => ({
      name: me?.github?.name || me?.github?.login || "You",
      avatarUrl: me?.github?.avatar_url ?? null,
    }),
    [me],
  );

  // Prior user turns (initial task + follow-ups) for ↑/↓ history recall.
  const userHistory = useMemo(
    () =>
      events
        .filter((e) => classify(e) === "user")
        .map((e) => e.text ?? "")
        .filter(Boolean),
    [events],
  );

  useEffect(() => {
    if (agentId == null) {
      setRunsState({ agentId: null, items: [] });
      return;
    }
    const load = () => {
      void api.agents
        .runs(agentId, { limit: 12 })
        .then((items) => setRunsState({ agentId, items }))
        .catch(() => setRunsState({ agentId, items: [] }));
    };
    load();
    // A run opens and closes as the agent works, so the rail — and the default
    // filter riding on it — follow the live stream rather than a manual reload.
    return eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      if (ev.agent_session_id == null || ev.agent_session_id === agentId) load();
    });
  }, [agentId]);

  const runs = runsState.agentId === agentId ? runsState.items : [];
  // `runChoice` is what the user asked for; this is what the transcript shows.
  // Left alone it tracks the newest run, so starting the agent again pulls the
  // view along instead of stranding you on a finished conversation.
  const latestRunId = runs[0]?.id ?? null;
  const runFilter = runChoice === "all" ? null : (runChoice ?? latestRunId);

  // Load the selected session's timeline, and keep it live on agent.changed.
  useEffect(() => {
    // Drop an optimistic echo when leaving the agent it targeted (it survives the
    // switch *into* its own freshly created session, cleared later when it lands).
    setPending((p) => (p && p.agentId !== agentId ? null : p));
    if (agentId == null) {
      setEvents([]);
      return;
    }
    void loadEvents(agentId, runFilter);
    return eventBus.subscribe((ev) => {
      if (ev.type !== "agent.changed") return;
      if (ev.agent_session_id == null || ev.agent_session_id === agentId) {
        void loadEvents(agentId, runFilter);
      }
    });
  }, [agentId, loadEvents, runFilter]);

  // A pinned run belongs to the agent it was chosen on — switching agents must
  // not leave the next transcript scoped to a run that isn't even theirs, or
  // stuck on "all runs" when the default is to read the latest.
  useEffect(() => {
    setRunChoice(null);
  }, [agentId]);

  // Drive the app-global sign-in banner when a turn surfaces an MCP server that
  // needs interactive auth (e.g. WorkIQ OAuth lapsed). The agent runtime emits a
  // synthetic `mcp_auth_required` event; mirror it into the shared store so the
  // user can re-authenticate inline instead of digging through Settings.
  useEffect(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const ev = events[i];
      if (ev.kind !== "mcp_auth_required") continue;
      const server =
        ev.tool_name ?? (ev.data?.server as string | undefined) ?? "workiq";
      mcpAuthStore.report(server, ev.text ?? "Sign-in required.");
      return;
    }
  }, [events]);

  // Snap the scroll container to its absolute bottom. We drive the container's
  // scrollTop directly (rather than scrollIntoView on an anchor) so height that
  // lands late — streaming text, markdown/code reflow, images — can't leave us
  // short of the real bottom.
  const scrollToBottom = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    box.scrollTop = box.scrollHeight;
  }, []);

  // While pinned, stay glued to the bottom as the content height changes. A
  // ResizeObserver catches every reflow (including ones that land after React
  // has already committed the events update), which the events-effect alone
  // kept missing — that was the "not quite reaching the bottom" symptom.
  useEffect(() => {
    const inner = innerRef.current;
    if (!inner) return;
    const ro = new ResizeObserver(() => {
      if (pinnedRef.current) scrollToBottom();
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [scrollToBottom]);

  // New steps: follow to the bottom while pinned, once layout has settled.
  useEffect(() => {
    if (!pinnedRef.current) return;
    requestAnimationFrame(scrollToBottom);
  }, [events, showPending, scrollToBottom]);

  // Jump straight to the bottom (and re-pin) when switching agents. The window
  // effect re-bounds `windowStart` to the most recent page once the new
  // timeline loads (pinnedRef is true here).
  useEffect(() => {
    pinnedRef.current = true;
    requestAnimationFrame(scrollToBottom);
  }, [agentId, scrollToBottom]);

  const onScroll = useCallback(() => {
    const box = scrollRef.current;
    if (!box) return;
    pinnedRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    // Near the top with older segments still hidden: reveal the next window,
    // anchoring the viewport so it doesn't jump as boxes mount above.
    if (box.scrollTop < 120 && windowStartRef.current > 0) {
      revealAnchorRef.current = box.scrollHeight;
      setWindowStart((s) => Math.max(0, s - AGENT_SEGMENT_WINDOW));
    }
  }, []);

  async function startTask(): Promise<void> {
    if (busy || !task.trim()) return;
    const message = task.trim();
    setBusy(true);
    setError(null);
    try {
      const created = await api.agents.create({
        task: message,
        topic_id: newTopicId,
        autonomy_enabled: newAutonomy,
        max_steps: newMaxSteps,
        approval_policy: newApprovalPolicy || null,
        start: newStart,
      });
      // Echo the prompt into the new session's transcript right away (only when
      // it's actually starting) so the hand-off feels immediate while the
      // runtime spins the agent up. A parked (waiting) agent has no live turn
      // yet, so skip the optimistic echo.
      if (newStart) setPending({ agentId: created.id, text: message });
      setTask("");
      setNewTopicId(null);
      setNewStart(true);
      onReload();
      onSelect(created.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function sendFollowUp(explicit?: string): Promise<void> {
    if (!selected || busy) return;
    const message = (explicit ?? followUp).trim();
    if (!message) return;
    // /clear erases the whole transcript on the backend — confirm first, mirroring
    // the topic/chat clear flow.
    if (
      /^\/clear\b/i.test(message) &&
      !window.confirm("Clear this agent conversation? The transcript will be erased.")
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    // Clear the composer and echo the prompt immediately so it's obvious the
    // message left, even before the backend records the real turn.
    if (explicit === undefined) setFollowUp("");
    setPending({ agentId: selected.id, text: message });
    try {
      await api.agents.send(selected.id, message);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPending(null);
      // Restore the draft so the user doesn't lose what they typed.
      if (explicit === undefined) setFollowUp(message);
    } finally {
      setBusy(false);
    }
  }

  async function stopAgent(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.cancel(selected.id);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function resume(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.resume(selected.id);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function startNow(): Promise<void> {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.start(selected.id);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function approve(
    requestId: string,
    decision: AgentPermissionDecisionValue,
  ): Promise<void> {
    if (!selected) return;
    setBusy(true);
    try {
      await api.agents.resolvePermission(selected.id, requestId, decision);
      onReload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Disabled: send the user to Settings to turn the feature on.
  if (!enabled) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <Bot size={28} className="text-muted" />
        <div className="max-w-sm space-y-1">
          <p className="text-sm font-medium">Agents mode is off</p>
          <p className="text-[12px] text-muted">
            Hand long-running tasks to an autonomous Copilot agent, attached to a
            topic or chat. Turn it on in Settings to get started.
          </p>
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white"
        >
          <SettingsIcon size={14} /> Open Settings
        </button>
      </div>
    );
  }

  // Enabled but nothing selected: the "start a new task" surface.
  if (!selected) {
    return (
      <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-3 p-8">
        <div className="flex items-center gap-2">
          <Bot size={18} />
          <h2 className="text-sm font-medium">Start an agent task</h2>
        </div>
        <p className="text-[12px] text-muted">
          Describe a task to hand off. The agent runs on its own and posts results
          back to a topic or chat when you attach one.
        </p>
        {!available && (
          <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-600 dark:text-amber-400">
            The Copilot runtime isn&apos;t available yet
            {unavailableReason ? `: ${unavailableReason}` : "."}
          </div>
        )}
        <label className="flex items-center gap-2 text-[12px] text-muted">
          Associate with topic
          <TopicPicker
            topics={topics}
            value={newTopicId}
            onChange={setNewTopicId}
            disabled={!available || busy}
            collections={collections}
          />
        </label>
        {/* Autonomy opt-in: turn the one-shot task into a background mission. */}
        <div className="rounded-lg border border-border bg-surface/50 p-2.5">
          <label className="flex items-start gap-2.5">
            <button
              type="button"
              role="switch"
              aria-checked={newAutonomy}
              disabled={!available || busy}
              onClick={() => setNewAutonomy((v) => !v)}
              className={`mt-0.5 flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 transition disabled:opacity-50 ${
                newAutonomy ? "bg-violet-500" : "bg-border"
              }`}
            >
              <span
                className={`h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${
                  newAutonomy ? "translate-x-3" : ""
                }`}
              />
            </button>
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-[12px] font-medium">
                <Radar size={13} className="text-violet-500" />
                Run autonomously
              </span>
              <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">
                Pursues the objective on its own, continuing between turns and
                pausing only when it finishes or needs your input.
              </span>
            </span>
          </label>
          {newAutonomy && (
            <label className="mt-2.5 flex items-center gap-2 pl-[38px] text-[11px] text-muted">
              Step budget
              <input
                type="number"
                min={1}
                max={100}
                value={newMaxSteps}
                disabled={!available || busy}
                onChange={(e) =>
                  setNewMaxSteps(Math.min(100, Math.max(1, Number(e.target.value) || 1)))
                }
                className="w-16 rounded border border-border bg-bg px-1.5 py-0.5 text-center tabular-nums outline-none focus:border-accent"
              />
              <span className="text-[10px]">continuations before it hands back</span>
            </label>
          )}
        </div>
        <div className="rounded-lg border border-border bg-surface/50 p-2.5">
          <label className="flex items-center gap-1.5 text-[12px] font-medium">
            <ShieldCheck size={13} className="text-violet-500" />
            Approval policy
          </label>
          <div className="mt-2">
            <Select
              fullWidth
              size="sm"
              ariaLabel="Approval policy for this agent"
              disabled={!available || busy}
              value={newApprovalPolicy}
              onChange={(v) => setNewApprovalPolicy(v as AgentApprovalPolicy | "")}
              options={[
                {
                  value: "",
                  label: `Inherit global default${
                    globalPolicyLabel ? ` — ${globalPolicyLabel}` : ""
                  }`,
                },
                ...APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label })),
              ]}
            />
          </div>
          <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
            {newApprovalPolicy
              ? (APPROVAL_POLICIES.find((p) => p.value === newApprovalPolicy)?.hint ?? "")
              : "Falls back to the global default set in Settings; takes effect on the agent's next turn."}
          </p>
        </div>
        <label
          className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-[12px] transition ${
            newStart ? "border-border bg-surface/50" : "border-violet-500/50 bg-violet-500/10"
          }`}
        >
          <input
            type="checkbox"
            className="mt-0.5 accent-violet-500"
            checked={!newStart}
            disabled={!available || busy}
            onChange={() => setNewStart((s) => !s)}
          />
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5 font-medium">
              <PauseCircle size={13} className="text-violet-500" />
              Create parked (don't run yet)
            </span>
            <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">
              Arm the agent in the <span className="font-medium">waiting</span> state
              and start it later — on a parent completing, a webhook, or a manual
              “Start now”.
            </span>
          </span>
        </label>
        {error && <p className="text-[11px] text-red-500">{error}</p>}
        <Composer
          value={task}
          onChange={setTask}
          onSend={() => void startTask()}
          onStop={() => {}}
          streaming={false}
          suggestions={taskSuggestions}
          userHistory={[]}
          speech={speech}
          interimText={interimText}
          height={composerHeight}
          onResizeStart={onComposerResize}
          autoFocus
          placeholder="e.g. Investigate the flaky CI test and propose a fix…"
          toolbarStart={<ComposerModelControls variant="agents" />}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="mx-auto flex h-full min-w-0 w-full max-w-3xl flex-col">
        {/* Scrollable workflow region. */}
        <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto px-5 py-3">
          <div ref={innerRef}>
        {error && <p className="mb-2 text-[11px] text-red-500">{error}</p>}

        {/* The transcript is scoped to one execution. Shown in the transcript
            itself, not only in the Runs rail, so the filter can't strand a user
            who has the insights panel collapsed. Silent on a single-run agent,
            where "the latest run" and "everything" are the same timeline. */}
        {runFilter != null && runs.length > 1 && (
          <div className="mb-3 flex items-center gap-1.5 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-[11px]">
            <History size={13} className="shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {runChoice == null
                ? `Showing the latest run (#${runFilter}). Earlier executions are hidden.`
                : `Showing run #${runFilter} only — other executions of this agent are hidden.`}
            </span>
            <button
              type="button"
              onClick={() => setRunChoice("all")}
              className="shrink-0 rounded px-1.5 py-0.5 font-medium text-accent hover:bg-accent/15"
            >
              Show all runs
            </button>
          </div>
        )}

        {selected.status === "needs_approval" && (
          <div className="mb-3 flex items-center gap-1.5 rounded border border-orange-500/30 bg-orange-500/10 px-2 py-1 text-[11px] text-orange-600 dark:text-orange-400">
            <ShieldQuestion size={13} /> Waiting for your approval — see the highlighted step
            below.
          </div>
        )}

        {/* Mission strip: objective + self-reported progress for autonomous
            agents, so the transcript reads as a tracked mission, not a chat. */}
        {selected.autonomy_enabled && selected.status !== "completed" && (
          <div className="mb-3 rounded-lg border border-violet-500/30 bg-violet-500/[0.06] px-2.5 py-2">
            <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-600 dark:text-violet-300">
              <Radar size={13} className="shrink-0" />
              <span className="min-w-0 flex-1 truncate">Objective: {selected.task_prompt}</span>
              <span className="shrink-0 rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] tabular-nums">
                step {selected.step_count}/{selected.max_steps}
              </span>
            </div>
            {selected.progress != null && (
              <div className="mt-1.5 flex flex-col gap-1">
                <div className="flex items-center justify-between text-[10px] text-muted">
                  <span className="min-w-0 truncate">{selected.progress_label ?? "Progress"}</span>
                  <span className="shrink-0 font-semibold tabular-nums">{selected.progress}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-border/60">
                  <span
                    className="block h-full rounded-full bg-violet-500 transition-all"
                    style={{
                      width: `${Math.min(100, Math.max(0, selected.progress))}%`,
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        )}

        {selected.status === "blocked" && (
          <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-700 dark:text-amber-300">
            <div className="flex items-center gap-1.5 font-semibold">
              <HelpCircle size={13} className="shrink-0" /> The agent needs your input
            </div>
            {selected.blocked_question && (
              <p className="mt-1 leading-relaxed text-text opacity-90">
                {selected.blocked_question}
              </p>
            )}
            <button
              type="button"
              onClick={focusComposer}
              className="mt-2 inline-flex items-center gap-1 rounded-md bg-amber-500/20 px-2 py-1 font-medium text-amber-900 transition hover:bg-amber-500/30 dark:text-amber-100"
            >
              <CornerDownRight size={12} /> Answer
            </button>
          </div>
        )}

        {selected.status === "waiting" && (
          <div className="mb-3 flex items-center justify-between gap-2 rounded border border-slate-500/30 bg-slate-500/10 px-2 py-1.5 text-[11px] text-slate-600 dark:text-slate-300">
            <span className="flex items-center gap-1.5">
              <PauseCircle size={13} /> Parked — armed for a trigger (a parent
              finishing, a webhook, or a manual start).
            </span>
            <button
              type="button"
              onClick={() => void startNow()}
              disabled={busy}
              className="flex items-center gap-1 rounded bg-slate-500/20 px-2 py-0.5 font-medium text-slate-800 hover:bg-slate-500/30 disabled:opacity-50 dark:text-slate-200"
            >
              <PlayCircle size={12} /> Start now
            </button>
          </div>
        )}

        {selected.status === "interrupted" && (
          <div className="mb-3 flex items-center justify-between gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
            <span className="flex items-center gap-1.5">
              <AlertTriangle size={13} /> This turn was interrupted before it
              finished.
            </span>
            {selected.active_prompt && (
              <button
                type="button"
                onClick={() => void resume()}
                disabled={busy}
                className="flex items-center gap-1 rounded bg-amber-500/20 px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-500/30 disabled:opacity-50 dark:text-amber-300"
              >
                <PlayCircle size={12} /> Resume
              </button>
            )}
          </div>
        )}

        {selected.error && (
          <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
            {selected.error}
          </div>
        )}

        {(() => {
          const { segments, trailingHooks, answerRows } = timeline;

          if (segments.length === 0 && trailingHooks.length === 0)
            return showPending ? (
              <div className="flex flex-col items-center">
                <div className="flex w-full justify-end">
                  <PendingUserBubble text={pending!.text} user={userPersona} />
                </div>
              </div>
            ) : (
              <p className="text-[11px] text-muted">No steps recorded yet.</p>
            );

          // Window the rendered segments: only those from `windowStart` onward
          // are mounted. `windowStart` is an absolute index, so keys stay stable
          // and appended steps never shift on-screen history; scrolling up
          // lowers it to reveal older boxes.
          const hiddenCount = Math.min(windowStart, segments.length);
          const shownSegments = hiddenCount > 0 ? segments.slice(hiddenCount) : segments;

          return (
            <div className="flex flex-col items-center">
              {hiddenCount > 0 && (
                <p className="mb-2 text-[11px] text-muted">
                  Scroll up to load earlier steps…
                </p>
              )}
              {shownSegments.map((seg, idx) => {
                const absoluteIdx = hiddenCount + idx;
                // Chat-style placement over the workflow spine: the user's own
                // prompts sit to the right, the assistant's answers to the left,
                // and everything else (system, tools, thinking, errors) centered.
                const align =
                  seg.row.type === "node" && seg.row.cat === "user"
                    ? "justify-end"
                    : seg.row.type === "node" && seg.row.cat === "assistant"
                      ? "justify-start"
                      : "justify-center";
                return (
                <Fragment key={absoluteIdx}>
                  {absoluteIdx === 0 ? (
                    seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} />
                  ) : (
                    <StepConnector hooks={seg.hooks} />
                  )}
                  <div className={`flex w-full ${align}`}>
                    {seg.row.type === "tool" ? (
                      <ToolBox
                        step={seg.row.step}
                        busy={busy}
                        onDecision={approve}
                      />
                    ) : seg.row.type === "node" ? (
                      <MessageNode
                        event={seg.row.ev}
                        category={seg.row.cat}
                        isLastAnswer={answerRows.has(seg.row)}
                        autonomy={selected.autonomy_enabled}
                        user={userPersona}
                        model={modelByEvent.get(seg.row.ev) ?? selected.model ?? null}
                        elapsedMs={elapsedByEvent.get(seg.row.ev) ?? null}
                        onPickSuggestion={(text) => void sendFollowUp(text)}
                        onReply={focusComposer}
                        suggestionsDisabled={
                          selected.status === "running" ||
                          selected.status === "pending" ||
                          selected.status === "needs_approval"
                        }
                      />
                    ) : null}
                  </div>
                  {seg.row.type === "node" &&
                    seg.row.cat === "assistant" &&
                    selected.autonomy_enabled &&
                    (() => {
                      const d = parseAgentDirectives(seg.row.ev.text ?? "");
                      // The terminal OBJECTIVE_COMPLETE is folded into the answer
                      // bubble itself (completion badge + summary), so it's no
                      // longer repeated as a spine node — only progress
                      // heartbeats remain on the spine.
                      if (d.complete != null || !d.progress) return null;
                      return (
                        <MissionMilestone progress={d.progress} at={seg.row.ev.at} />
                      );
                    })()}
                </Fragment>
                );
              })}
              {trailingHooks.length > 0 && <HookGutter hooks={trailingHooks} />}
              {selected && <AgentDeliverables agent={selected} />}
              {showPending && (
                <>
                  <StepConnector hooks={[]} />
                  <div className="flex w-full justify-end">
                    <PendingUserBubble text={pending!.text} user={userPersona} />
                  </div>
                </>
              )}
            </div>
          );
        })()}
          </div>
      </div>

      {/* Follow-up: always visible. While a turn is in flight the input is
          disabled and the send button becomes a Stop control, matching the
          topic/chat composer pattern. */}
      {(() => {
        const turnActive =
          selected.status === "running" ||
          selected.status === "pending" ||
          selected.status === "needs_approval";
        // While the send request is still in flight (`busy`) the turn hasn't
        // reported active yet — lock the input so the prompt can't be sent twice,
        // but don't flash a Stop control until there's actually a turn to stop.
        const sending = busy && pending != null;
        return (
          <div ref={composerWrapRef} className="shrink-0 border-t border-border px-5 py-3">
            <Composer
              value={followUp}
              onChange={setFollowUp}
              onSend={() => void sendFollowUp()}
              onStop={() => void stopAgent()}
              streaming={turnActive}
              disabled={turnActive || sending}
              suggestions={followUpSuggestions}
              userHistory={userHistory}
              speech={speech}
              interimText={interimText}
              height={composerHeight}
              onResizeStart={onComposerResize}
              placeholder={
                turnActive
                  ? "Agent is working… use Stop to interrupt"
                  : sending
                    ? "Sending…"
                    : "Send a follow-up message…"
              }
              toolbarStart={<ComposerModelControls variant="agents" />}
            />
          </div>
        );
      })()}
      </div>
      <AgentInsightsPanel
        showPrefs={showPrefs}
        toggleShow={toggleShow}
        events={events}
        model={selected.model ?? null}
        agent={selected}
        runs={runs}
        runFilter={runFilter}
        onRunFilterChange={setRunChoice}
      />
    </div>
  );
}
