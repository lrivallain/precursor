import { useEffect, useRef, useState } from "react";
import { Check, Lightbulb, Loader2, Pause, Play, X } from "lucide-react";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

// Poll cadence and gating for live proactive assist.
const DEBOUNCE_MS = 9000;
// Only re-check once at least this many new lines have been spoken.
const MIN_NEW_SEGMENTS = 2;

interface Suggestion {
  id: number;
  text: string;
  at: number;
}

/**
 * Proactive assist, live: quietly watches the discussion and only surfaces a
 * card when the model judges there's something worth helping with. Most of the
 * time it stays idle. Auto-monitoring can be paused; a manual "Check now" forces
 * an immediate look.
 */
export function AssistSection({
  sessionId,
  segmentCount,
  canRun,
}: {
  sessionId: number;
  segmentCount: number;
  canRun: boolean;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(true);

  const runningRef = useRef(false);
  const lastCheckedCountRef = useRef(0);
  const lastTextRef = useRef("");

  async function check(force: boolean): Promise<void> {
    if (runningRef.current || !canRun) return;
    if (!force && segmentCount < lastCheckedCountRef.current + MIN_NEW_SEGMENTS) return;
    runningRef.current = true;
    lastCheckedCountRef.current = segmentCount;
    setLoading(true);
    setError(null);
    try {
      const res = await api.suggestMeeting(sessionId);
      const text = res.suggestion.trim();
      // Only surface genuinely new help — skip repeats of the last suggestion.
      if (res.has_suggestion && text && text !== lastTextRef.current) {
        lastTextRef.current = text;
        setSuggestions((prev) => [{ id: Date.now(), text, at: Date.now() }, ...prev].slice(0, 12));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't check for suggestions.");
    } finally {
      runningRef.current = false;
      setLoading(false);
    }
  }

  // Live loop: re-check (debounced) as new lines arrive while auto is on.
  useEffect(() => {
    if (!auto || !canRun) return;
    if (segmentCount < lastCheckedCountRef.current + MIN_NEW_SEGMENTS) return;
    const t = setTimeout(() => void check(false), DEBOUNCE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segmentCount, auto, canRun]);

  function dismiss(id: number): void {
    setSuggestions((prev) => prev.filter((s) => s.id !== id));
  }

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
            onClick={() => setAuto((v) => !v)}
            data-tooltip={auto ? "Pause monitoring" : "Resume monitoring"}
            aria-label={auto ? "Pause monitoring" : "Resume monitoring"}
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-accent"
          >
            {auto ? <Pause size={13} /> : <Play size={13} />}
          </button>
          <button
            type="button"
            onClick={() => void check(true)}
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
              <div
                key={s.id}
                className="rounded border border-accent/40 bg-accent/5 px-3 py-2"
              >
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
                    onClick={() => dismiss(s.id)}
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
