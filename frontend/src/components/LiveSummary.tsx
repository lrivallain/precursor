import { useEffect, useRef, useState } from "react";
import { Check, Copy, Loader2, X } from "lucide-react";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

interface Props {
  sessionId: number;
  topicId: number | null;
  topicTitle: string | null;
  onClose: () => void;
  onPosted?: () => void;
}

/**
 * Generate a markdown meeting summary and optionally append it to the linked
 * topic. Opened on demand or auto-drafted when a session ends. The generated
 * text is editable before posting.
 */
export function LiveSummary({ sessionId, topicId, topicTitle, onClose, onPosted }: Props) {
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const [posted, setPosted] = useState(false);
  const [copied, setCopied] = useState(false);
  const [preview, setPreview] = useState(true);
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void (async () => {
      try {
        const res = await api.summarizeMeeting(sessionId);
        setText(res.summary);
      } catch (e) {
        setError(
          e instanceof Error ? e.message : "Couldn't generate a summary — try recording more.",
        );
      } finally {
        setLoading(false);
      }
    })();
  }, [sessionId]);

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }

  async function post(): Promise<void> {
    if (!text.trim() || posting || topicId == null) return;
    setPosting(true);
    setError(null);
    try {
      await api.postMeetingSummary(sessionId, text);
      setPosted(true);
      onPosted?.();
      setTimeout(onClose, 900);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't post to the topic.");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-[min(760px,100%)] flex-col overflow-hidden rounded-lg border border-border bg-bg shadow-xl">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">Meeting summary</h2>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPreview((p) => !p)}
              className="rounded px-2 py-1 text-[12px] text-muted hover:bg-surface"
            >
              {preview ? "Edit" : "Preview"}
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded p-1 text-muted hover:bg-surface"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {loading ? (
            <div className="flex h-40 items-center justify-center gap-2 text-sm text-muted">
              <Loader2 size={16} className="animate-spin" /> Generating summary…
            </div>
          ) : error && !text ? (
            <p className="text-sm text-red-500">{error}</p>
          ) : preview ? (
            <Markdown className="prose-sm">{text}</Markdown>
          ) : (
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              className="h-72 w-full resize-y rounded border border-border bg-surface px-3 py-2 font-mono text-[13px] text-text outline-none focus:border-accent"
            />
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <div className="min-w-0 text-[12px] text-muted">
            {topicId == null ? (
              <span>Attach a topic to this session to post the summary.</span>
            ) : (
              <span className="truncate">
                Posts as a message into <strong>{topicTitle ?? "the topic"}</strong>.
              </span>
            )}
            {error && text && <span className="ml-2 text-red-500">{error}</span>}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              disabled={!text}
              className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-50"
            >
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={() => void post()}
              disabled={!text || posting || topicId == null || posted}
              className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
            >
              {posted ? <Check size={14} /> : null}
              {posted ? "Posted" : posting ? "Posting…" : "Post to topic"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
