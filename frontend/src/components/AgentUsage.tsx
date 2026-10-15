import { useMemo } from "react";
import type { ReactNode } from "react";
import { BarChart3 } from "lucide-react";
import type { AgentEvent } from "../lib/types";
import { CompactContextButton } from "./CompactContext";
import { ContextBreakdown } from "./ContextBreakdown";

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
  /** The SDK's split of the window; null until it reports one. */
  contextSystem: number | null;
  contextTools: number | null;
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
  let contextSystem: number | null = null;
  let contextTools: number | null = null;
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
        const sys = ev.data?.system_tokens;
        const tools = ev.data?.tool_definitions_tokens;
        contextSystem = typeof sys === "number" ? sys : null;
        contextTools = typeof tools === "number" ? tools : null;
      }
    } else if (kind === "compaction" && ev.data?.success !== false) {
      // The window shrinks the moment a compaction lands, before the next
      // context_usage report.
      const post = ev.data?.post_compaction_tokens;
      if (typeof post === "number" && Number.isFinite(post)) contextUsed = post;
      const limit = numField(ev.data, "token_limit");
      if (limit > 0) contextLimit = limit;
    }
  }
  return {
    lastInput,
    lastOutput,
    totalInput,
    totalOutput,
    rounds,
    contextUsed,
    contextLimit,
    contextSystem,
    contextTools,
  };
}

/** Memoized token-usage summary derived from an agent's event stream. */
export function useAgentUsage(events: AgentEvent[]): AgentUsage {
  return useMemo(() => computeAgentUsage(events), [events]);
}

function formatTokens(n: number): string {
  return n.toLocaleString();
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

export function AgentUsageSection({
  events,
  model,
  compact,
}: {
  events: AgentEvent[];
  model: string | null;
  /** The /compact control; omitted where the agent can't be compacted. */
  compact?: { busy: boolean; disabled: boolean; disabledReason?: string; run: () => void };
}) {
  const usage = useAgentUsage(events);
  const pct =
    usage.contextLimit !== null && usage.contextUsed !== null && usage.contextLimit > 0
      ? (usage.contextUsed / usage.contextLimit) * 100
      : null;
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
            <ContextBreakdown
              used={usage.contextUsed}
              limit={usage.contextLimit}
              system={usage.contextSystem}
              systemTools={usage.contextTools}
              model={model}
            />
          )}
          {compact && (
            <CompactContextButton
              pct={pct}
              busy={compact.busy}
              disabled={compact.disabled}
              disabledReason={compact.disabledReason}
              onCompact={compact.run}
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
