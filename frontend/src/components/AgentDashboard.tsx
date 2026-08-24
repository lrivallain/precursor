/**
 * The agent dashboard — a control-tower cockpit for the whole fleet.
 *
 * Instead of opening agents one at a time like alternative topics, this is the
 * default agents-mode view: a row of KPI stat tiles over an urgency-sorted grid
 * of monitor cards, grouped into swimlanes (Needs you / Working / Idle · done)
 * so the fleet reads as a control tower rather than a wall of identical cards.
 * Blocked agents (waiting on the human) float to the top with a warm amber
 * treatment; working agents show their live tool and sub-agent fan-out;
 * scheduled agents show their next run. Inspired by Voyageur's control-tower /
 * attention-router layout.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ComponentType } from "react";
import {
  AlertCircle,
  ArrowUpRight,
  Bot,
  CalendarClock,
  CircleDot,
  Coins,
  Gauge,
  HelpCircle,
  Loader2,
  Mail,
  MessagesSquare,
  Plus,
  Radar,
  Search,
  ShieldQuestion,
  Upload,
  Workflow as WorkflowIcon,
  Wrench,
  X,
} from "lucide-react";
import type {
  AgentInboxItem,
  AgentMetrics,
  AgentSession,
  TransferImportResult,
  WorkflowSummary,
} from "../lib/types";
import { api } from "../lib/api";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { AgentMedallion } from "./AgentMedallion";
import { ImportDialog } from "./ImportDialog";
import {
  AGENT_STATUS_DOT,
  agentIsActive,
  agentNeedsAttention,
  agentRelativeTime,
  sortAgentsByUrgency,
} from "../lib/agents";

interface AgentDashboardProps {
  agents: AgentSession[];
  onSelect: (id: number) => void;
  onNew: () => void;
  /** Refresh + focus the agent an imported YAML file produced. */
  onImported?: (result: TransferImportResult) => void;
  onOpenWorkflow?: (workflowId: number) => void;
}

// Compact token count for the header rollup (e.g. 12.4k, 3.1M).
function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatNextRun(iso: string): string {
  const when = new Date(iso);
  const now = new Date();
  const sameDay =
    when.getFullYear() === now.getFullYear() &&
    when.getMonth() === now.getMonth() &&
    when.getDate() === now.getDate();
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${when.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

// Lane an agent belongs to on the board — the same three-way split the KPI
// tiles count, so the numbers up top match the columns below.
type LaneKey = "attention" | "working" | "quiet";

function laneOf(a: AgentSession): LaneKey {
  if (agentNeedsAttention(a)) return "attention";
  if (agentIsActive(a)) return "working";
  return "quiet";
}

const LANE_META: Record<LaneKey, { label: string; dot: string }> = {
  attention: { label: "Needs you", dot: "bg-orange-500" },
  working: { label: "Working", dot: "bg-sky-500" },
  quiet: { label: "Idle · done", dot: "bg-emerald-500" },
};

// The KPI tiles double as filters. Three map straight to a lane; "scheduled"
// is a cross-lane filter (an agent in any lane can carry a schedule).
type FilterKey = LaneKey | "scheduled";

const FILTER_LABEL: Record<FilterKey, string> = {
  attention: "Needs you",
  working: "Working",
  quiet: "Idle · done",
  scheduled: "Scheduled",
};

export function AgentDashboard({
  agents,
  onSelect,
  onNew,
  onImported,
  onOpenWorkflow,
}: AgentDashboardProps) {
  const ordered = useMemo(() => sortAgentsByUrgency(agents), [agents]);
  const [importing, setImporting] = useState(false);

  // Which KPI tile is acting as a filter, if any. Clicking a tile toggles it:
  // click once to narrow to that lane, click again to clear.
  const [filter, setFilter] = useState<FilterKey | null>(null);
  const toggleFilter = (key: FilterKey) =>
    setFilter((cur) => (cur === key ? null : key));

  // Free-text narrowing by agent name, stacked on top of the KPI tile filter.
  const [query, setQuery] = useState("");
  const search = query.trim().toLowerCase();
  const clearFilters = () => {
    setFilter(null);
    setQuery("");
  };

  // Aggregate observability + unified inbox. Polled here (not derived from the
  // agent list) so the header shows fleet-wide token spend and concurrency
  // headroom, and the inbox surfaces the live parked permission per gate.
  const [metrics, setMetrics] = useState<AgentMetrics | null>(null);
  const [inbox, setInbox] = useState<AgentInboxItem[]>([]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [m, i] = await Promise.all([api.agents.metrics(), api.agents.inbox()]);
        if (!alive) return;
        setMetrics(m);
        setInbox(i);
      } catch {
        // Non-fatal: the dashboard still renders from the agent list.
      }
    };
    void load();
    const timer = window.setInterval(load, 5000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
    // Re-poll immediately when the fleet size/shape changes.
  }, [agents.length]);

  const summary = useMemo(() => {
    let attention = 0;
    let working = 0;
    let quiet = 0;
    let scheduled = 0;
    for (const a of agents) {
      const lane = laneOf(a);
      if (lane === "attention") attention += 1;
      else if (lane === "working") working += 1;
      else quiet += 1;
      if (a.schedule?.enabled) scheduled += 1;
    }
    return { attention, working, quiet, scheduled, total: agents.length };
  }, [agents]);

  // Group the already-urgency-sorted list into lanes, preserving order within
  // each. A lane filter narrows to that single lane; the "scheduled" filter
  // keeps every lane but only its scheduled agents; the search box narrows by
  // name on top of both. Only non-empty lanes render.
  const lanes = useMemo(() => {
    const groups: Record<LaneKey, AgentSession[]> = {
      attention: [],
      working: [],
      quiet: [],
    };
    for (const a of ordered) {
      if (filter === "scheduled" && !a.schedule?.enabled) continue;
      if (search && !a.title.toLowerCase().includes(search)) continue;
      groups[laneOf(a)].push(a);
    }
    return (["attention", "working", "quiet"] as LaneKey[])
      .filter((key) => filter === null || filter === "scheduled" || key === filter)
      .map((key) => ({ key, ...LANE_META[key], agents: groups[key] }))
      .filter((lane) => lane.agents.length > 0);
  }, [ordered, filter, search]);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-gradient-to-b from-transparent to-surface/30">
      {/* Control-tower header: title + KPI stat tiles. */}
      <div className="border-b border-border px-4 pb-3 pt-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-violet-500/15 text-violet-500">
            <Bot size={16} />
          </span>
          <h1 className="text-sm font-semibold">Agent fleet</h1>
          {summary.total > 0 && (
            <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium tabular-nums text-muted">
              {summary.total}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {agents.length > 0 && (
              <div className="relative">
                <Search
                  size={14}
                  className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted"
                />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter agents…"
                  aria-label="Filter agents by name"
                  className="w-44 rounded-lg border border-border bg-surface py-1.5 pl-8 pr-7 text-sm outline-none transition focus:border-accent focus:ring-1 focus:ring-accent/40 sm:w-56"
                />
                {search && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted transition hover:bg-white/5 hover:text-text"
                    aria-label="Clear name filter"
                    data-tooltip="Clear name filter"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            )}
            {onImported && (
              <button
                type="button"
                onClick={() => setImporting(true)}
                title="Import an agent from a YAML file"
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition hover:bg-white/5 hover:text-fg"
              >
                <Upload size={15} />
                Import
              </button>
            )}
            <button
              type="button"
              onClick={onNew}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white shadow-sm transition hover:opacity-90 hover:shadow"
            >
              <Plus size={15} />
              New agent
            </button>
          </div>
        </div>

        {importing && onImported && (
          <ImportDialog
            expect="agent"
            onClose={() => setImporting(false)}
            onImported={onImported}
          />
        )}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <StatTile
            icon={AlertCircle}
            label="Need you"
            value={summary.attention}
            accent="text-orange-500"
            bar="bg-orange-500"
            glow={summary.attention > 0}
            active={filter === "attention"}
            onClick={() => toggleFilter("attention")}
          />
          <StatTile
            icon={Loader2}
            label="Working"
            value={summary.working}
            accent="text-sky-500"
            bar="bg-sky-500"
            spin={summary.working > 0}
            active={filter === "working"}
            onClick={() => toggleFilter("working")}
          />
          <StatTile
            icon={CircleDot}
            label="Idle · done"
            value={summary.quiet}
            accent="text-emerald-500"
            bar="bg-emerald-500"
            active={filter === "quiet"}
            onClick={() => toggleFilter("quiet")}
          />
          <StatTile
            icon={CalendarClock}
            label="Scheduled"
            value={summary.scheduled}
            accent="text-accent"
            bar="bg-accent"
            active={filter === "scheduled"}
            onClick={() => toggleFilter("scheduled")}
          />
        </div>
        {metrics && (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
            <span
              className="inline-flex items-center gap-1.5"
              data-tooltip="Agents running now vs the concurrency ceiling"
            >
              <Gauge size={12} className="text-sky-500" />
              <span className="font-medium text-text">
                {metrics.running_now}/{metrics.max_concurrent}
              </span>
              running
            </span>
            <span
              className="inline-flex items-center gap-1.5"
              data-tooltip="Total tokens consumed across the fleet"
            >
              <Coins size={12} className="text-amber-500" />
              <span className="font-medium text-text">
                {formatTokens(metrics.total_input_tokens + metrics.total_output_tokens)}
              </span>
              tokens
            </span>
            {metrics.completed > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <CircleDot size={12} className="text-emerald-500" />
                <span className="font-medium text-text">{metrics.completed}</span> done
              </span>
            )}
            {metrics.failed > 0 && (
              <span className="inline-flex items-center gap-1.5 text-rose-500">
                <AlertCircle size={12} />
                <span className="font-medium">{metrics.failed}</span> failed
              </span>
            )}
          </div>
        )}
      </div>

      {inbox.length > 0 && (
        <div className="border-b border-border bg-orange-500/[0.04] px-4 py-2.5">
          <div className="mb-1.5 flex items-center gap-1.5">
            <ShieldQuestion size={13} className="text-orange-500" />
            <h2 className="text-[11px] font-semibold uppercase tracking-wide text-orange-600 dark:text-orange-300">
              Inbox
            </h2>
            <span className="rounded-full bg-orange-500/15 px-1.5 text-[10px] font-semibold tabular-nums text-orange-600 dark:text-orange-300">
              {inbox.length}
            </span>
          </div>
          <ul className="flex flex-wrap gap-2">
            {inbox.map((item) => (
              <li key={`${item.kind}-${item.agent_id}`}>
                <InboxChip item={item} onSelect={() => onSelect(item.agent_id)} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4">
        {ordered.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-violet-500/10 text-violet-500 ring-1 ring-violet-500/20">
              <Bot size={30} />
            </span>
            <p className="max-w-sm text-sm text-muted">
              No agents yet. Start one and it runs in the background — track it
              here without babysitting the timeline.
            </p>
            <button
              type="button"
              onClick={onNew}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-3.5 py-2 text-sm font-medium text-white shadow-sm transition hover:opacity-90 hover:shadow"
            >
              <Plus size={15} />
              New agent
            </button>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {(filter || search) && (
              <div className="flex items-center gap-2 text-xs text-muted">
                <span>
                  Filtered to{" "}
                  {filter && (
                    <span className="font-semibold text-text">{FILTER_LABEL[filter]}</span>
                  )}
                  {filter && search && " · "}
                  {search && <span className="font-semibold text-text">“{query.trim()}”</span>}
                </span>
                <button
                  type="button"
                  onClick={clearFilters}
                  className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 font-medium transition hover:border-accent/60 hover:text-text"
                >
                  <X size={11} />
                  Clear
                </button>
              </div>
            )}
            {lanes.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface text-muted ring-1 ring-border">
                  <Radar size={22} />
                </span>
                <p className="text-sm text-muted">
                  No agents match{" "}
                  <span className="font-medium text-text">
                    {search ? `“${query.trim()}”` : filter ? FILTER_LABEL[filter] : "this view"}
                  </span>
                  .
                </p>
                <button
                  type="button"
                  onClick={clearFilters}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm font-medium transition hover:border-accent/60"
                >
                  <X size={14} />
                  Clear filters
                </button>
              </div>
            ) : (
              <div className="flex flex-col gap-6">
                {lanes.map((lane) => (
                  <section key={lane.key}>
                    <div className="mb-2.5 flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${lane.dot}`} />
                      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">
                        {lane.label}
                      </h2>
                      <span className="rounded-full bg-surface px-1.5 text-[10px] font-medium tabular-nums text-muted">
                        {lane.agents.length}
                      </span>
                      <span className="ml-1 h-px flex-1 bg-border" />
                    </div>
                    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                      {lane.agents.map((a) => (
                        <li key={a.id}>
                          <AgentCard
                            agent={a}
                            onSelect={() => onSelect(a.id)}
                            onOpenWorkflow={onOpenWorkflow}
                          />
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const INBOX_META: Record<
  AgentInboxItem["kind"],
  { label: string; icon: ComponentType<{ size?: number; className?: string }>; cls: string }
> = {
  needs_approval: {
    label: "Approve",
    icon: AlertCircle,
    cls: "border-orange-500/40 bg-orange-500/10 text-orange-600 dark:text-orange-300",
  },
  blocked: {
    label: "Answer",
    icon: HelpCircle,
    cls: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  },
  budget: {
    label: "Budget",
    icon: Coins,
    cls: "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-300",
  },
};

function InboxChip({ item, onSelect }: { item: AgentInboxItem; onSelect: () => void }) {
  const meta = INBOX_META[item.kind];
  const Icon = meta.icon;
  return (
    <button
      type="button"
      onClick={onSelect}
      data-tooltip={item.detail ?? undefined}
      className={`inline-flex max-w-[18rem] items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium transition hover:brightness-105 ${meta.cls}`}
    >
      <Icon size={12} className="shrink-0" />
      <span className="shrink-0 font-semibold">{meta.label}</span>
      <span className="min-w-0 truncate opacity-90">{item.title}</span>
    </button>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
  accent,
  bar,
  glow = false,
  spin = false,
  active = false,
  onClick,
}: {
  icon: ComponentType<{ size?: number; className?: string }>;
  label: string;
  value: number;
  accent: string;
  bar: string;
  glow?: boolean;
  spin?: boolean;
  active?: boolean;
  onClick?: () => void;
}) {
  const dim = value === 0;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      data-tooltip={active ? `Showing ${label} — click to clear` : `Show only ${label}`}
      className={`relative w-full overflow-hidden rounded-xl border bg-surface/70 px-3.5 py-2.5 text-left transition hover:border-accent/60 hover:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
        active
          ? "border-accent/70 bg-surface ring-2 ring-accent/40"
          : glow
            ? "border-orange-500/50 shadow-[0_0_0_1px_rgba(249,115,22,0.15)]"
            : "border-border"
      }`}
    >
      <span className={`absolute inset-x-0 top-0 h-0.5 ${bar} ${dim && !active ? "opacity-30" : ""}`} />
      <div className="flex items-center justify-between">
        <span className={`text-2xl font-semibold tabular-nums ${dim ? "text-muted" : ""}`}>
          {value}
        </span>
        <Icon
          size={16}
          className={`${dim && !active ? "text-muted opacity-60" : accent} ${spin ? "animate-spin" : ""}`}
        />
      </div>
      <div className="mt-0.5 text-[11px] font-medium text-muted">{label}</div>
    </button>
  );
}

function AgentCard({
  agent,
  onSelect,
  onOpenWorkflow,
}: {
  agent: AgentSession;
  onSelect: () => void;
  onOpenWorkflow?: (workflowId: number) => void;
}) {
  const attention = agentNeedsAttention(agent);
  // The pipelines using this agent, fetched the first time the chip is hovered
  // or clicked — the card only ships a count, not the names.
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [showWorkflows, setShowWorkflows] = useState(false);
  const loadWorkflows = useCallback(async () => {
    if (workflows !== null) return workflows;
    try {
      const rows = await api.agents.workflows(agent.id);
      setWorkflows(rows);
      return rows;
    } catch {
      setWorkflows([]);
      return [];
    }
  }, [agent.id, workflows]);
  const waiting = agent.status === "needs_approval";
  const blocked = agent.status === "blocked";
  const showProgress =
    agent.progress != null && agent.status !== "completed" && agent.status !== "cancelled";
  const link =
    agent.topic_id != null
      ? { icon: MessagesSquare, label: `Topic #${agent.topic_id}` }
      : agent.chat_id != null
        ? { icon: Mail, label: `Chat #${agent.chat_id}` }
        : null;
  const LinkIcon = link?.icon;

  return (
    // A div rather than a button: the card hosts its own controls (the workflow
    // chip), and a button may not nest interactive children.
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`group relative flex h-full w-full cursor-pointer flex-col gap-2 overflow-hidden rounded-xl border p-3.5 pl-4 text-left shadow-sm transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/60 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
        attention
          ? "border-orange-500/50 bg-gradient-to-br from-orange-500/10 to-surface ring-1 ring-orange-500/20"
          : "border-border bg-gradient-to-br from-surface to-bg"
      }`}
    >
      {/* Status accent rail down the left edge. */}
      <span
        className={`absolute inset-y-0 left-0 w-1 ${AGENT_STATUS_DOT[agent.status]} ${
          attention ? "" : "opacity-70"
        }`}
      />

      <div className="flex items-center gap-2">
        <AgentMedallion status={agent.status} activeToolCount={agent.active_tool_count} size={12} />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold">{agent.title}</span>
        {agent.autonomy_enabled && (
          <span
            data-tooltip="Autonomous — pursues its objective on its own"
            className="inline-flex items-center gap-1 rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-violet-500"
          >
            <Radar size={9} />
            Auto
          </span>
        )}
        <AgentStatusBadge status={agent.status} />
      </div>

      {/* Mission progress — the agent's own sense of how far it's come. */}
      {showProgress && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between text-[10px] text-muted">
            <span className="min-w-0 truncate">{agent.progress_label ?? "Progress"}</span>
            <span className="shrink-0 font-semibold tabular-nums">{agent.progress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-border/60">
            <span
              className="block h-full rounded-full bg-violet-500 transition-all"
              style={{ width: `${Math.min(100, Math.max(0, agent.progress ?? 0))}%` }}
            />
          </div>
        </div>
      )}

      {waiting && (
        <div className="flex items-start gap-1.5 rounded-lg bg-orange-500/10 px-2 py-1.5 text-[11px] text-orange-600 dark:text-orange-300">
          <AlertCircle size={13} className="mt-px shrink-0" />
          <span className="min-w-0">
            <span className="font-semibold">Waiting for you</span>
            {agent.pending_permission?.title ? ` · ${agent.pending_permission.title}` : ""}
          </span>
        </div>
      )}

      {blocked && (
        <div className="flex items-start gap-1.5 rounded-lg bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-300">
          <HelpCircle size={13} className="mt-px shrink-0" />
          <span className="min-w-0">
            <span className="font-semibold">Needs input</span>
            {agent.blocked_question ? (
              <span className="mt-0.5 line-clamp-2 font-normal opacity-90">
                {agent.blocked_question}
              </span>
            ) : null}
          </span>
        </div>
      )}

      {/* Live activity: the current tool + sub-agent fan-out while working, with
          the agent's own words about what it's doing underneath. */}
      {agent.active_tool ? (
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 rounded-lg bg-violet-500/5 px-2 py-1 text-[11px] text-violet-500 dark:text-violet-300">
            <Wrench size={12} className="shrink-0 animate-pulse" />
            <span className="min-w-0 truncate font-medium">{agent.active_tool}</span>
            {agent.active_tool_count > 1 && (
              <span className="shrink-0 rounded-full bg-violet-500/15 px-1.5 text-[10px] font-semibold">
                ×{agent.active_tool_count}
              </span>
            )}
          </div>
          {agent.active_narration && (
            <p className="line-clamp-2 px-2 text-[11px] italic leading-relaxed text-muted">
              {agent.active_narration}
            </p>
          )}
        </div>
      ) : agent.active_narration ? (
        <p className="line-clamp-2 rounded-lg bg-violet-500/5 px-2 py-1 text-[11px] italic leading-relaxed text-violet-500 dark:text-violet-300">
          {agent.active_narration}
        </p>
      ) : (
        agent.result_summary && (
          <p className="line-clamp-2 text-[11px] leading-relaxed text-muted">{agent.result_summary}</p>
        )
      )}

      {showWorkflows && workflows && workflows.length > 0 && (
        <div className="space-y-0.5 rounded-lg bg-accent/5 p-1">
          {workflows.map((w) => (
            <button
              key={w.id}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onOpenWorkflow?.(w.id);
              }}
              className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] text-muted transition hover:bg-accent/10 hover:text-accent"
            >
              {w.icon ? (
                <span className="shrink-0">{w.icon}</span>
              ) : (
                <WorkflowIcon size={11} className="shrink-0" />
              )}
              <span className="truncate">{w.name}</span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/60 pt-2 text-[10px] text-muted">
        <span className="inline-flex items-center gap-1">
          {agentIsActive(agent) ? <Loader2 size={10} className="animate-spin" /> : null}
          {agentRelativeTime(agent.last_activity_at ?? agent.created_at)}
        </span>
        {agent.schedule?.enabled && agent.schedule.next_run_at && (
          <span className="inline-flex items-center gap-1 text-accent">
            <CalendarClock size={10} />
            {formatNextRun(agent.schedule.next_run_at)}
          </span>
        )}
        {agent.unread_count > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-1.5 font-semibold text-accent">
            <Mail size={10} />
            {agent.unread_count > 9 ? "9+" : agent.unread_count} new
          </span>
        )}
        {link && LinkIcon && (
          <span className="inline-flex items-center gap-1">
            <LinkIcon size={10} />
            {link.label}
          </span>
        )}
        {agent.workflow_count > 0 && (
          <button
            type="button"
            // One pipeline: go straight there. Several: name them first.
            onClick={async (e) => {
              e.stopPropagation();
              if (showWorkflows) {
                setShowWorkflows(false);
                return;
              }
              const rows = await loadWorkflows();
              if (rows.length === 1) onOpenWorkflow?.(rows[0].id);
              else setShowWorkflows(true);
            }}
            onMouseEnter={() => void loadWorkflows()}
            className={`inline-flex items-center gap-1 rounded-full px-1.5 font-semibold transition ${
              showWorkflows
                ? "bg-accent/15 text-accent"
                : "text-muted hover:bg-accent/10 hover:text-accent"
            }`}
            aria-label={`Used in ${agent.workflow_count} workflow${
              agent.workflow_count === 1 ? "" : "s"
            }`}
            data-tooltip={`Used in ${agent.workflow_count} workflow${
              agent.workflow_count === 1 ? "" : "s"
            }`}
          >
            <WorkflowIcon size={10} />
            {agent.workflow_count}
          </button>
        )}
        {agent.model && <span className="truncate opacity-80">{agent.model}</span>}
        <ArrowUpRight
          size={13}
          className="ml-auto -translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-70"
        />
      </div>
    </div>
  );
}
