import { useState } from "react";
import { ChevronDown, ChevronRight, FoldVertical, Loader2, Shrink, Undo2 } from "lucide-react";
import { Markdown } from "./Markdown";
import type { Message } from "../lib/types";

/** Context-window fill (percent) from which the stats panels suggest compacting. */
export const COMPACT_NUDGE_PERCENT = 80;

function compactInt(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return (n / 1000).toFixed(1) + "k";
  if (n < 1_000_000) return Math.round(n / 1000) + "k";
  return (n / 1_000_000).toFixed(1) + "M";
}

/**
 * The stats panels' "Compact" control. Above {@link COMPACT_NUDGE_PERCENT} it
 * becomes an amber call-out: compaction is never automatic for topics and
 * chats, so this is where the user learns it's time.
 */
export function CompactContextButton({
  pct,
  busy,
  disabled,
  disabledReason,
  onCompact,
}: {
  /** Context-window fill, 0–100, or null when unknown. */
  pct: number | null;
  busy: boolean;
  disabled?: boolean;
  disabledReason?: string;
  onCompact: () => void;
}) {
  const nudge = pct !== null && pct >= COMPACT_NUDGE_PERCENT;
  const button = (
    <button
      type="button"
      onClick={onCompact}
      disabled={busy || disabled}
      data-tooltip={
        disabled && disabledReason
          ? disabledReason
          : "Summarise the conversation so far. Older messages stay visible;\nthe model sees the summary instead (/compact)."
      }
      className={`inline-flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        nudge
          ? "border-amber-500/60 bg-amber-500/15 text-amber-700 hover:bg-amber-500/25 dark:text-amber-300"
          : "border-border text-muted hover:bg-surface hover:text-text"
      }`}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : <Shrink size={12} />}
      {busy ? "Compacting…" : "Compact context"}
    </button>
  );
  if (!nudge) return <div className="mt-2">{button}</div>;
  return (
    <div
      className="mt-2 space-y-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-[11px] text-amber-800 dark:text-amber-200"
      role="status"
    >
      <p>
        Context is {Math.round(pct)}% full. Compact it to keep going without the
        oldest turns falling out of the window.
      </p>
      {button}
    </div>
  );
}

/**
 * A compaction marker in the transcript: a divider noting that the model now
 * sees the summary instead of everything above, with the summary on demand and
 * an Undo (deleting the marker restores the full history for the model).
 */
export function CompactionMarker({
  message,
  onUndo,
}: {
  message: Message;
  onUndo?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const cost = (message.prompt_tokens ?? 0) + (message.completion_tokens ?? 0);
  return (
    <div className="my-2" data-testid="compaction-marker">
      <div className="flex items-center gap-2 text-[11px] text-muted">
        <span className="h-px flex-1 bg-border" />
        <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 py-0.5">
          <FoldVertical size={12} />
          <span className="font-medium text-text/80">Context compacted</span>
          <span aria-hidden>·</span>
          <span>the model sees the summary instead of the messages above</span>
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>
      <div className="mt-1 flex items-center justify-center gap-3 text-[11px] text-muted">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-1 hover:text-text"
          aria-expanded={open}
        >
          {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {open ? "Hide summary" : "Show summary"}
        </button>
        {onUndo && (
          <button
            type="button"
            onClick={onUndo}
            className="inline-flex items-center gap-1 hover:text-text"
            data-tooltip="Remove this summary: the model sees the full history again"
          >
            <Undo2 size={12} />
            Undo
          </button>
        )}
        {cost > 0 && (
          <span data-tooltip={message.model ?? undefined}>
            {compactInt(cost)} tokens to summarise
          </span>
        )}
      </div>
      {open && (
        <div className="mx-auto mt-2 max-w-full rounded-lg border border-border bg-surface/60 px-3 py-2">
          <Markdown className="text-sm leading-relaxed">{message.content}</Markdown>
        </div>
      )}
    </div>
  );
}
