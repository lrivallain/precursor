import { useState } from "react";
import { HeartHandshake, Loader2 } from "lucide-react";
import { apiErrorMessage } from "../lib/api";
import { saveTelemetryChoice, useTelemetryStatus } from "../lib/telemetry";

/**
 * Asks once whether this install may send anonymous usage data. Nothing is sent
 * before a "yes"; either answer is saved and the banner does not come back.
 * Settings → Privacy changes the answer later.
 */
export function TelemetryInvite() {
  const status = useTelemetryStatus();
  const [showPreview, setShowPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!status || !status.available || status.decided) return null;

  async function answer(enabled: boolean): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await saveTelemetryChoice(enabled);
    } catch (e) {
      setError(apiErrorMessage(e));
      setBusy(false);
    }
  }

  return (
    <div
      role="region"
      aria-label="Anonymous usage data"
      className="border-b border-sky-500/30 bg-sky-500/10 px-4 py-2 text-[12px] text-sky-900 dark:text-sky-100"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <HeartHandshake size={15} className="shrink-0 text-sky-600 dark:text-sky-400" aria-hidden />
        <p className="min-w-0 flex-1">
          <strong className="font-medium">Help improve Precursor?</strong> Send an anonymous
          heartbeat once a day: the version, the OS, which features are on and rough usage counts.
          Never your conversations, files, names or accounts.
          {error && <span className="ml-2 text-red-600 dark:text-red-400">{error}</span>}
        </p>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            onClick={() => setShowPreview((v) => !v)}
            className="rounded px-2 py-1 text-sky-800 hover:bg-sky-500/15 dark:text-sky-200"
          >
            {showPreview ? "Hide" : "See what is sent"}
          </button>
          <button
            type="button"
            onClick={() => void answer(true)}
            disabled={busy}
            className="inline-flex items-center gap-1 rounded bg-sky-600 px-2.5 py-1 font-medium text-white hover:bg-sky-700 disabled:opacity-50"
          >
            {busy && <Loader2 size={12} className="animate-spin" aria-hidden />}
            Share anonymous usage
          </button>
          <button
            type="button"
            onClick={() => void answer(false)}
            disabled={busy}
            className="rounded px-2 py-1 text-sky-800 hover:bg-sky-500/15 disabled:opacity-50 dark:text-sky-200"
          >
            No thanks
          </button>
        </div>
      </div>
      {showPreview && (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
          {status.preview}
        </pre>
      )}
    </div>
  );
}
