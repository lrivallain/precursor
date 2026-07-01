import { Clock, Cpu, Timer } from "lucide-react";

/**
 * Subtle metadata line shown under a message: the timestamp, and — when the
 * turn came from an assistant — the model id + generation time. Pieces with no
 * data are simply omitted. Renders dimmed by default and brightens when the
 * surrounding bubble/node is hovered.
 *
 * Shared by the topic/chat transcript (`MessageBubble`) and the Agents-mode
 * workflow timeline (`AgentView`) so both surfaces show the same recap.
 */
export function MessageMeta({
  createdAt,
  model,
  elapsedMs,
  align = "start",
  hoverGroup = "default",
}: {
  createdAt?: string | null;
  // Assistant turns only: the LLM model id that produced the answer.
  model?: string | null;
  // Assistant turns only: wall-clock generation time in milliseconds.
  elapsedMs?: number | null;
  align?: "start" | "end";
  // Which hover group brightens the line. `MessageBubble` uses the default
  // `group`; `AgentView` nodes use a scoped `group/node`.
  hoverGroup?: "default" | "node";
}) {
  const time = formatTimestamp(createdAt);
  const hasModelInfo = model || elapsedMs != null;
  if (!time && !hasModelInfo) return null;
  const hoverCls =
    hoverGroup === "node" ? "group-hover/node:opacity-100" : "group-hover:opacity-100";
  return (
    <div
      className={`flex items-center gap-1.5 px-1 text-[10px] text-muted opacity-70 transition-opacity duration-150 ${hoverCls} ${
        align === "end" ? "justify-end" : "justify-start"
      }`}
    >
      {model && (
        <span
          className="inline-flex max-w-[16rem] items-center gap-1 rounded-full border border-border bg-bg/60 px-1.5 py-0.5 font-medium text-text/70"
          title={`Model: ${model}`}
        >
          <Cpu size={10} className="shrink-0 text-accent/70" />
          <span className="truncate">{model}</span>
        </span>
      )}
      {elapsedMs != null && (
        <span className="inline-flex items-center gap-1" title="Generation time">
          <Timer size={10} className="shrink-0" />
          {formatElapsed(elapsedMs)}
        </span>
      )}
      {time && (
        <span
          className="inline-flex items-center gap-1"
          title={createdAt ? new Date(createdAt).toLocaleString() : undefined}
        >
          <Clock size={10} className="shrink-0" />
          {time}
        </span>
      )}
    </div>
  );
}

export function formatTimestamp(value?: string | null): string | null {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return time;
  // Older turns prepend a compact date; include the year only when it differs
  // so today/this-year labels stay short.
  const date = d.toLocaleDateString([], {
    day: "2-digit",
    month: "short",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${date}, ${time}`;
}

export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}
