import { useMemo } from "react";
import type { ReactNode } from "react";
import { BarChart3 } from "lucide-react";
import type { AgentEvent } from "../lib/types";

// Per-agent token usage distilled from the workflow timeline. `usage` events
// (one per metered LLM round) carry input/output tokens; `context_usage` events
// report the live context-window occupancy. Mirrors the conversation-stats
// aside on topics/chats, but sourced from the agent's own event stream.
export interface AgentUsage {
  lastInput: number;
  lastOutput: number;
  totalInput: number;
  totalOutput: number;
  rounds: number;
  contextUsed: number | null;
  contextLimit: number | null;
}

function numField(data: Record<string, unknown> | null, key: string): number {
  const v = data?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

export function computeAgentUsage(events: AgentEvent[]): AgentUsage {
  let lastInput = 0;
  let lastOutput = 0;
  let totalInput = 0;
  let totalOutput = 0;
  let rounds = 0;
  let contextUsed: number | null = null;
  let contextLimit: number | null = null;
  for (const ev of events) {
    const kind = ev.kind.toLowerCase();
    if (kind === "usage") {
      const input = numField(ev.data, "input_tokens");
      const output = numField(ev.data, "output_tokens");
      // Skip rounds that reported no tokens so they don't reset "last turn".
      if (input === 0 && output === 0) continue;
      totalInput += input;
      totalOutput += output;
      lastInput = input;
      lastOutput = output;
      rounds += 1;
    } else if (kind === "context_usage") {
      const limit = numField(ev.data, "token_limit");
      if (limit > 0) {
        contextUsed = numField(ev.data, "current_tokens");
        contextLimit = limit;
      }
    }
  }
  return { lastInput, lastOutput, totalInput, totalOutput, rounds, contextUsed, contextLimit };
}

/** Memoized token-usage summary derived from an agent's event stream. */
export function useAgentUsage(events: AgentEvent[]): AgentUsage {
  return useMemo(() => computeAgentUsage(events), [events]);
}

function formatTokens(n: number): string {
  return n.toLocaleString();
}

function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

function UsageStat({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: string;
  emphasis?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted">{label}</span>
      <span className={`tabular-nums ${emphasis ? "font-semibold" : ""}`}>{value}</span>
    </div>
  );
}

function UsageGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

// Context-window occupancy bar — green/amber/rose as it fills, like the
// topic/chat conversation stats.
function ContextWindowBar({
  used,
  limit,
  model,
}: {
  used: number;
  limit: number;
  model: string | null;
}) {
  const pct = Math.min(100, Math.max(0, (used / limit) * 100));
  const tone = pct >= 85 ? "bg-rose-500" : pct >= 60 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Context window</div>
      <div
        className="h-2 w-full overflow-hidden rounded bg-border"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        aria-label={`Context window used: ${used} of ${limit} tokens`}
      >
        <div className={`h-full ${tone} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 flex justify-between text-[11px] tabular-nums text-muted">
        <span>
          {compactTokens(used)} / {compactTokens(limit)}
        </span>
        <span>{pct.toFixed(pct < 10 ? 1 : 0)}%</span>
      </div>
      {model && (
        <div className="mt-0.5 truncate text-[10px] text-muted" title={model}>
          {model}
        </div>
      )}
    </div>
  );
}

export function AgentUsageSection({
  events,
  model,
}: {
  events: AgentEvent[];
  model: string | null;
}) {
  const usage = useAgentUsage(events);
  const hasUsage = usage.rounds > 0 || usage.contextLimit !== null;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <BarChart3 size={14} />
        <span>Usage</span>
      </div>
      {!hasUsage ? (
        <p className="text-[11px] text-muted">No usage reported yet.</p>
      ) : (
        <>
          {usage.contextLimit !== null && usage.contextUsed !== null && (
            <ContextWindowBar
              used={usage.contextUsed}
              limit={usage.contextLimit}
              model={model}
            />
          )}
          <UsageGroup title="Last turn">
            <UsageStat label="Input" value={formatTokens(usage.lastInput)} />
            <UsageStat label="Output" value={formatTokens(usage.lastOutput)} />
          </UsageGroup>
          <UsageGroup title="Cumulative">
            <UsageStat label="Input" value={formatTokens(usage.totalInput)} />
            <UsageStat label="Output" value={formatTokens(usage.totalOutput)} />
            <UsageStat
              label="Total"
              value={formatTokens(usage.totalInput + usage.totalOutput)}
              emphasis
            />
            <UsageStat label="Rounds" value={String(usage.rounds)} />
          </UsageGroup>
        </>
      )}
    </div>
  );
}
