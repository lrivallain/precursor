import { useState } from "react";
import { Lightbulb, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

/**
 * Proactive assist (on demand): asks the model to propose a concrete solution or
 * answer to whatever is currently being discussed, grounded on the meeting.
 */
export function AssistSection({ sessionId, canRun }: { sessionId: number; canRun: boolean }) {
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.suggestMeeting(sessionId);
      setText(res.suggestion);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't get a suggestion.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">
          Proactive assist
        </div>
        <button
          type="button"
          onClick={() => void run()}
          disabled={loading || !canRun}
          data-tooltip={canRun ? undefined : "Record some of the meeting first"}
          className="ml-auto inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1.5 text-[12px] text-white disabled:opacity-50"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <Lightbulb size={13} />}
          {text ? "Suggest again" : "Suggest"}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-[12px] text-red-500">{error}</p>}
        {loading && !text ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Thinking about the current discussion…
          </div>
        ) : text ? (
          <div className="prose prose-sm dark:prose-invert max-w-none text-[13px]">
            <Markdown>{text}</Markdown>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <Lightbulb size={18} className="mb-2 opacity-70" aria-hidden="true" />
            <p className="max-w-sm">
              On demand, propose a concrete answer or solution to what&apos;s being discussed
              right now — grounded on the transcript, insights and attached context.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
