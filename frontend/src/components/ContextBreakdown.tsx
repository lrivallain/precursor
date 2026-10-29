import { COMPACT_BUFFER_FRACTION } from "./CompactContext";

interface ContextBreakdownProps {
  /** Tokens in the window now. */
  used: number;
  limit: number;
  /** System prompt tokens, when known. */
  system?: number | null;
  /** The app's own tool definitions. */
  systemTools?: number | null;
  /** Tool definitions of the attached MCP servers. */
  mcpTools?: number | null;
  /** `used` itself is an estimate (e.g. right after a compaction). */
  estimated?: boolean;
  /** The system and tool sizes are estimates, not provider-reported counts. */
  partsEstimated?: boolean;
  model?: string | null;
}

interface Segment {
  key: string;
  label: string;
  tokens: number;
  color: string;
  tooltip: string;
}

function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "K";
  if (n < 1_000_000) return Math.round(n / 1000) + "K";
  return (n / 1_000_000).toFixed(1) + "M";
}

function formatPct(pct: number): string {
  if (pct > 0 && pct < 1) return "<1%";
  return `${Math.round(pct)}%`;
}

/**
 * Splits the window into what fills it (system prompt, tool definitions,
 * messages) and what is left (free space, then the buffer kept clear above the
 * compaction threshold). The fixed parts are scaled down when their estimates
 * overshoot the reported total, so the bar always adds up to `used`.
 */
export function splitContext({
  used,
  limit,
  system,
  systemTools,
  mcpTools,
}: Pick<ContextBreakdownProps, "used" | "limit" | "system" | "systemTools" | "mcpTools">) {
  const total = Math.min(Math.max(0, used), limit);
  let sys = Math.max(0, system ?? 0);
  let tools = Math.max(0, systemTools ?? 0);
  let mcp = Math.max(0, mcpTools ?? 0);
  const fixed = sys + tools + mcp;
  if (fixed > total && fixed > 0) {
    const k = total / fixed;
    sys = Math.round(sys * k);
    tools = Math.round(tools * k);
    mcp = Math.round(mcp * k);
  }
  const messages = Math.max(0, total - sys - tools - mcp);
  const left = limit - total;
  const buffer = Math.min(left, Math.round(limit * COMPACT_BUFFER_FRACTION));
  return { total, sys, tools, mcp, messages, buffer, free: left - buffer };
}

export function ContextBreakdown(props: ContextBreakdownProps) {
  const { used, limit, estimated, partsEstimated, model } = props;
  const s = splitContext(props);
  const pct = (s.total / limit) * 100;
  const tone =
    pct >= 85
      ? "text-rose-600 dark:text-rose-400"
      : pct >= 60
        ? "text-amber-600 dark:text-amber-400"
        : "";
  const approx = estimated ? "≈ " : "";
  const partApprox = partsEstimated || estimated ? "≈ " : "";
  const segments: Segment[] = [
    {
      key: "system",
      label: "System prompt",
      tokens: s.sys,
      color: "bg-blue-500",
      tooltip: "Instructions, role, memory and context sent with every turn.",
    },
    {
      key: "tools",
      label: "System tools",
      tokens: s.tools,
      color: "bg-violet-500",
      tooltip: "Definitions of the tools the app provides itself.",
    },
    {
      key: "mcp",
      label: "MCP tools",
      tokens: s.mcp,
      color: "bg-pink-500",
      tooltip: "Definitions of the tools of the attached MCP servers.",
    },
    {
      key: "messages",
      label: s.sys + s.tools + s.mcp > 0 ? "Messages" : "Used",
      tokens: s.messages,
      color: "bg-emerald-500",
      tooltip: "The conversation: prompts, replies and tool results.",
    },
    {
      key: "free",
      label: "Free space",
      tokens: s.free,
      color: "bg-border",
      tooltip: "Room left before the compaction threshold.",
    },
    {
      key: "buffer",
      label: "Buffer",
      tokens: s.buffer,
      color: "bg-muted/50",
      tooltip: `The last ${Math.round(COMPACT_BUFFER_FRACTION * 100)}% of the window. Compact before the conversation reaches it.`,
    },
  ];
  const shown = segments.filter((seg) => seg.tokens > 0);
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Context window</div>
      <div
        className="flex h-2 w-full gap-px overflow-hidden rounded bg-border"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        aria-label={`Context window used: ${used} of ${limit} tokens`}
      >
        {shown.map((seg) => (
          <div
            key={seg.key}
            className={`h-full ${seg.color} transition-all`}
            style={{ width: `${(seg.tokens / limit) * 100}%` }}
            data-tooltip={`${seg.label}: ${seg.tokens.toLocaleString()} tokens`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex items-baseline justify-between text-[11px] tabular-nums">
        <span className={`font-medium ${tone}`}>
          {approx}
          {formatPct(pct)} used
        </span>
        <span className="text-muted">
          {approx}
          {compactTokens(s.total)} / {compactTokens(limit)}
        </span>
      </div>
      <ul className="mt-1.5 space-y-0.5 text-[11px]">
        {shown.map((seg) => (
          <li key={seg.key} className="flex items-center gap-1.5" data-tooltip={seg.tooltip}>
            <span className={`h-2 w-2 shrink-0 rounded-full ${seg.color}`} />
            <span className="truncate text-muted">{seg.label}</span>
            <span className="tabular-nums text-muted">{formatPct((seg.tokens / limit) * 100)}</span>
            <span className="ml-auto tabular-nums">
              {seg.key === "free" || seg.key === "buffer" ? "" : partApprox}
              {compactTokens(seg.tokens)}
            </span>
          </li>
        ))}
      </ul>
      {model && (
        <div className="mt-1 truncate text-[10px] text-muted" data-tooltip={model}>
          {model}
        </div>
      )}
    </div>
  );
}
