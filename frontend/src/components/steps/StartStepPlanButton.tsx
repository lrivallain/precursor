import { useEffect, useState } from "react";
import { ListChecks, Loader2 } from "lucide-react";
import { apiErrorMessage } from "../../lib/api";
import { startStepPlanFromTopic } from "../../lib/stepPlans";

/**
 * Topic header button: split the topic's latest ask into a step plan and open
 * it in Step by step. The model proposes the first steps, so it spins for a
 * while; a failure keeps the (empty) plan server-side and is shown on the button.
 */
export function StartStepPlanButton({ topicId }: { topicId: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!error) return;
    const t = window.setTimeout(() => setError(null), 8000);
    return () => window.clearTimeout(t);
  }, [error]);

  async function start(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await startStepPlanFromTopic(topicId);
    } catch (err) {
      setError(apiErrorMessage(err, "Couldn't split the ask into steps"));
    } finally {
      setBusy(false);
    }
  }

  const tooltip = busy
    ? "Splitting the latest ask into steps…"
    : "Step by step\nSplit the latest ask in this topic into steps, work each one, then consolidate an answer";

  return (
    <span className="relative shrink-0">
      <button
        type="button"
        className={`p-2 rounded hover:bg-surface ${error ? "text-red-500" : ""}`}
        aria-label="Step by step"
        aria-busy={busy}
        data-tooltip={tooltip}
        disabled={busy}
        onClick={() => void start()}
      >
        {busy ? <Loader2 size={18} className="animate-spin" /> : <ListChecks size={18} />}
      </button>
      {error && (
        <span
          role="alert"
          className="absolute right-0 top-full z-50 mt-1 w-72 rounded-lg border border-red-500/40 bg-bg p-2.5 text-xs text-red-500 shadow-lg"
        >
          Couldn't start a step plan: {error} A plan may still exist in Step by step.
        </span>
      )}
    </span>
  );
}
