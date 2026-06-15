import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, BarChart3 } from "lucide-react";
import { streamStore } from "../lib/streamStore";
import { api } from "../lib/api";
import { modelsStore, useCurrentModel } from "../lib/modelsStore";
import type { Message } from "../lib/types";

interface ChatStatsPanelProps {
  topicId: number;
  messages: Message[];
}

interface RoundStat {
  id: number;
  prompt: number;
  completion: number;
}

interface Totals {
  prompt: number;
  completion: number;
  total: number;
  rounds: RoundStat[];
  lastPrompt: number;
  lastCompletion: number;
}

function formatInt(n: number): string {
  return n.toLocaleString();
}

function compactInt(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

function computeTotals(messages: Message[]): Totals {
  const rounds: RoundStat[] = [];
  let prompt = 0;
  let completion = 0;
  for (const m of messages) {
    if (m.role !== "assistant") continue;
    const p = m.prompt_tokens ?? 0;
    const c = m.completion_tokens ?? 0;
    if (m.prompt_tokens == null && m.completion_tokens == null) continue;
    prompt += p;
    completion += c;
    rounds.push({ id: m.id, prompt: p, completion: c });
  }
  const last = rounds.length > 0 ? rounds[rounds.length - 1] : null;
  return {
    prompt,
    completion,
    total: prompt + completion,
    rounds,
    lastPrompt: last?.prompt ?? 0,
    lastCompletion: last?.completion ?? 0,
  };
}

function countByRole(messages: Message[]): Record<string, number> {
  const counts: Record<string, number> = {
    user: 0,
    assistant: 0,
    tool: 0,
    system: 0,
  };
  for (const m of messages) {
    counts[m.role] = (counts[m.role] ?? 0) + 1;
  }
  return counts;
}

function chars(messages: Message[]): number {
  let n = 0;
  for (const m of messages) n += m.content.length;
  return n;
}

const STORAGE_KEY = "precursor:chat-stats:collapsed";

export function ChatStatsPanel({ topicId, messages }: ChatStatsPanelProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  });
  const [showRounds, setShowRounds] = useState(false);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  // Resolve the active model so we can show context-window usage. Cheap:
  // settings + catalog are cached on the store after the first fetch.
  useEffect(() => {
    void modelsStore.ensureLoaded();
    let cancelled = false;
    api
      .getSettings()
      .then((s) => {
        if (!cancelled) modelsStore.applySettings(s);
      })
      .catch(() => {
        /* non-fatal: just no context-window bar */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const model = useCurrentModel();
  const contextWindow = model?.context_window ?? null;

  const totals = useMemo(() => computeTotals(messages), [messages]);
  const roles = useMemo(() => countByRole(messages), [messages]);
  const charCount = useMemo(() => chars(messages), [messages]);
  const liveUsage = streamStore.lastUsage(topicId);
  const lastInput = liveUsage?.prompt_tokens ?? totals.lastPrompt;
  const lastOutput = liveUsage?.completion_tokens ?? totals.lastCompletion;

  if (collapsed) {
    return (
      <aside className="border-l border-border bg-surface/40 flex flex-col items-center py-2 px-1 w-9 shrink-0">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="p-1.5 rounded hover:bg-surface text-muted"
          data-tooltip="Show conversation stats"
          aria-label="Show conversation stats"
        >
          <ChevronLeft size={16} />
        </button>
        <BarChart3 size={16} className="mt-2 text-muted" />
      </aside>
    );
  }

  return (
    <aside className="border-l border-border bg-surface/30 w-64 shrink-0 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <BarChart3 size={14} />
          <span>Conversation</span>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="p-1 rounded hover:bg-surface text-muted"
          data-tooltip="Collapse stats"
          aria-label="Collapse stats"
        >
          <ChevronRight size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4 text-sm">
        {contextWindow !== null && contextWindow > 0 && (
          <ContextWindowSection
            used={lastInput}
            limit={contextWindow}
            modelName={model?.name ?? model?.id ?? "model"}
          />
        )}

        <Section title="Last turn">
          <Stat label="Input" value={formatInt(lastInput)} unit="tokens" />
          <Stat label="Output" value={formatInt(lastOutput)} unit="tokens" />
        </Section>

        <Section title="Cumulative">
          <Stat label="Input" value={formatInt(totals.prompt)} unit="tokens" />
          <Stat
            label="Output"
            value={formatInt(totals.completion)}
            unit="tokens"
          />
          <Stat
            label="Total"
            value={formatInt(totals.total)}
            unit="tokens"
            emphasis
          />
        </Section>

        <Section title="Messages">
          <Stat label="User" value={String(roles.user ?? 0)} />
          <Stat label="Assistant" value={String(roles.assistant ?? 0)} />
          {roles.tool > 0 && <Stat label="Tool" value={String(roles.tool)} />}
          {roles.system > 0 && (
            <Stat label="System" value={String(roles.system)} />
          )}
          <Stat label="Characters" value={compactInt(charCount)} />
        </Section>

        {totals.rounds.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setShowRounds((v) => !v)}
              className="w-full text-left text-xs uppercase tracking-wide text-muted hover:text-foreground"
            >
              {showRounds ? "▾" : "▸"} Rounds ({totals.rounds.length})
            </button>
            {showRounds && (
              <ul className="mt-1 space-y-0.5 text-xs">
                {totals.rounds.map((r, i) => (
                  <li
                    key={r.id}
                    className="flex justify-between gap-2 text-muted"
                  >
                    <span>#{i + 1}</span>
                    <span className="tabular-nums">
                      {compactInt(r.prompt)} in / {compactInt(r.completion)} out
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted mb-1">
        {title}
      </div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function ContextWindowSection({
  used,
  limit,
  modelName,
}: {
  used: number;
  limit: number;
  modelName: string;
}) {
  const pct = Math.min(100, Math.max(0, (used / limit) * 100));
  // Cool zone < 60%, warm 60–85%, hot > 85%.
  const tone =
    pct >= 85
      ? "bg-rose-500"
      : pct >= 60
        ? "bg-amber-500"
        : "bg-emerald-500";
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted mb-1">
        Context window
      </div>
      <div
        className="h-2 w-full rounded bg-border overflow-hidden"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        aria-label={`Context window used: ${used} of ${limit} tokens`}
      >
        <div
          className={`h-full ${tone} transition-all`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="mt-1 flex justify-between text-xs text-muted tabular-nums">
        <span>
          {compactInt(used)} / {compactInt(limit)}
        </span>
        <span>{pct.toFixed(pct < 10 ? 1 : 0)}%</span>
      </div>
      <div
        className="mt-0.5 text-[10px] text-muted truncate"
        title={modelName}
      >
        {modelName}
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  unit,
  emphasis,
}: {
  label: string;
  value: string;
  unit?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="flex justify-between items-baseline gap-2">
      <span className="text-muted">{label}</span>
      <span
        className={`tabular-nums ${emphasis ? "font-semibold" : ""}`}
        title={unit ? `${value} ${unit}` : value}
      >
        {value}
        {unit && <span className="text-muted text-xs ml-1">{unit}</span>}
      </span>
    </div>
  );
}
