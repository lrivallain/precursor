import { Check, Lightbulb, Loader2, Pause, Play, X } from "lucide-react";
import { Markdown } from "./Markdown";

export interface Suggestion {
  id: number;
  text: string;
  at: number;
}

/**
 * Proactive assist view. Presentational: the monitoring loop runs in LiveView so
 * it keeps watching the discussion even when this tab isn't the active pane, and
 * only surfaces a card when the assistant can genuinely help.
 */
export function AssistSection({
  suggestions,
  loading,
  error,
  auto,
  canRun,
  onToggleAuto,
  onCheckNow,
  onDismiss,
}: {
  suggestions: Suggestion[];
  loading: boolean;
  error: string | null;
  auto: boolean;
  canRun: boolean;
  onToggleAuto: () => void;
  onCheckNow: () => void;
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
          <Lightbulb size={12} /> Proactive assist
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-1 text-[11px] text-muted">
            {loading ? (
              <>
                <Loader2 size={11} className="animate-spin" /> Checking…
              </>
            ) : auto ? (
              "Monitoring"
            ) : (
              "Paused"
            )}
          </span>
          <button
            type="button"
            onClick={onToggleAuto}
            data-tooltip={auto ? "Pause monitoring" : "Resume monitoring"}
            aria-label={auto ? "Pause monitoring" : "Resume monitoring"}
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-accent"
          >
            {auto ? <Pause size={13} /> : <Play size={13} />}
          </button>
          <button
            type="button"
            onClick={onCheckNow}
            disabled={loading || !canRun}
            className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1.5 text-[12px] text-white disabled:opacity-50"
          >
            <Lightbulb size={13} /> Check now
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-[12px] text-red-500">{error}</p>}
        {suggestions.length > 0 ? (
          <div className="space-y-2">
            {suggestions.map((s) => (
              <div key={s.id} className="rounded border border-accent/40 bg-accent/5 px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5">
                  <Lightbulb size={12} className="text-accent" />
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted">
                    Suggestion
                  </span>
                  <span className="ml-auto text-[10px] text-muted">
                    {new Date(s.at).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <button
                    type="button"
                    onClick={() => onDismiss(s.id)}
                    aria-label="Dismiss"
                    className="text-muted hover:text-red-500"
                  >
                    <X size={12} />
                  </button>
                </div>
                <div className="prose prose-sm dark:prose-invert max-w-none text-[13px]">
                  <Markdown>{s.text}</Markdown>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <Check size={18} className="mb-2 opacity-70" aria-hidden="true" />
            <p className="max-w-sm">
              {canRun
                ? "Watching the discussion. Suggestions appear here only when the assistant can genuinely help — otherwise it stays quiet."
                : "Suggestions appear here once the meeting gets going."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
