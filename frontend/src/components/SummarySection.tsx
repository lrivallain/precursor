import { useState } from "react";
import { Check, Copy, Eye, Loader2, Pencil, Plus, RefreshCw, Send, X } from "lucide-react";
import type { MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

interface Props {
  session: MeetingSession;
  onUpdated: (session: MeetingSession) => void;
  text: string;
  setText: (text: string) => void;
  generating: boolean;
  error: string | null;
  onGenerate: () => void;
  /** Speaker-derived names not yet in the attendee list. */
  suggestedAttendees: string[];
  topicTitle: string | null;
  canGenerate: boolean;
}

/**
 * Summary tab: an editable attendee list plus the generated markdown recap,
 * with copy and post-to-topic actions. Attendees seed from the renamed speakers
 * (and, later, the linked M365 meeting) and are folded into the summary.
 */
export function SummarySection({
  session,
  onUpdated,
  text,
  setText,
  generating,
  error,
  onGenerate,
  suggestedAttendees,
  topicTitle,
  canGenerate,
}: Props) {
  const [mode, setMode] = useState<"edit" | "preview">("preview");
  const [newAttendee, setNewAttendee] = useState("");
  const [copied, setCopied] = useState(false);
  const [posting, setPosting] = useState(false);
  const [posted, setPosted] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);

  const attendees = session.attendees ?? [];
  const canPost = session.topic_id != null;
  const postedAt = session.summary_posted_at;
  const postedLabel = postedAt
    ? new Date(postedAt).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : null;

  async function saveAttendees(next: string[]): Promise<void> {
    try {
      const updated = await api.meetings.setAttendees(session.id, next);
      onUpdated(updated);
    } catch {
      /* non-fatal */
    }
  }

  function addAttendee(name: string): void {
    const n = name.trim();
    if (!n || attendees.includes(n)) return;
    void saveAttendees([...attendees, n]);
    setNewAttendee("");
  }

  function removeAttendee(name: string): void {
    void saveAttendees(attendees.filter((a) => a !== name));
  }

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
    if (!text.trim() || posting || !canPost) return;
    setPosting(true);
    setPostError(null);
    try {
      const res = await api.meetings.postSummary(session.id, text);
      setPosted(true);
      setTimeout(() => setPosted(false), 2000);
      // Persist that the recap reached the topic so the green marker survives
      // reloads and tab switches (the backend stamped summary_posted_at).
      onUpdated({
        ...session,
        summary: text,
        summary_posted_at: res.posted_at,
        summary_posted_topic_id: res.topic_id,
      });
    } catch (e) {
      setPostError(e instanceof Error ? e.message : "Couldn't post to the topic.");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Attendees */}
      <div className="border-b border-border px-3 py-2">
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
          Attendees
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {attendees.map((a) => (
            <span
              key={a}
              className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-0.5 text-[12px]"
            >
              {a}
              <button
                type="button"
                onClick={() => removeAttendee(a)}
                aria-label={`Remove ${a}`}
                className="text-muted hover:text-red-500"
              >
                <X size={11} />
              </button>
            </span>
          ))}
          <input
            value={newAttendee}
            onChange={(e) => setNewAttendee(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addAttendee(newAttendee);
              }
            }}
            placeholder="Add attendee…"
            className="min-w-[7rem] flex-1 rounded border border-border bg-surface px-2 py-0.5 text-[12px] outline-none focus:border-accent"
          />
        </div>
        {suggestedAttendees.length > 0 && (
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            <span className="text-[11px] text-muted">Suggested:</span>
            {suggestedAttendees.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => addAttendee(s)}
                className="inline-flex items-center gap-0.5 rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent hover:text-accent"
              >
                <Plus size={10} /> {s}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <button
          type="button"
          onClick={onGenerate}
          disabled={generating || !canGenerate}
          className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
        >
          {generating ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <RefreshCw size={12} />
          )}
          {text ? "Refresh" : "Generate"}
        </button>
        <div className="flex items-center gap-0.5 text-xs">
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`inline-flex items-center gap-1 rounded px-2 py-1 ${
              mode === "edit" ? "bg-surface text-text" : "text-muted hover:text-text"
            }`}
          >
            <Pencil size={11} /> Edit
          </button>
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`inline-flex items-center gap-1 rounded px-2 py-1 ${
              mode === "preview" ? "bg-surface text-text" : "text-muted hover:text-text"
            }`}
          >
            <Eye size={11} /> Preview
          </button>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {postedAt && (
            <span
              className="inline-flex items-center gap-1 text-[12px] text-emerald-600 dark:text-emerald-400"
              title={postedLabel ? `Posted ${postedLabel}` : undefined}
            >
              <Check size={13} />
              Posted{topicTitle ? ` to ${topicTitle}` : ""}
            </span>
          )}
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
            disabled={!text || posting || !canPost}
            data-tooltip={canPost ? undefined : "Attach a topic to post"}
            className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            {posted ? <Check size={14} /> : <Send size={14} />}
            {posted ? "Posted" : posting ? "Posting…" : postedAt ? "Post again" : "Post to topic"}
          </button>
        </div>
      </div>

      {(error || postError) && (
        <div className="border-b border-border bg-red-500/10 px-3 py-1.5 text-[12px] text-red-500">
          {postError ?? error}
        </div>
      )}

      {/* Body */}
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {generating && !text ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" /> Generating summary…
          </div>
        ) : mode === "edit" ? (
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="The generated summary appears here…"
            className="h-full min-h-[12rem] w-full resize-none rounded border border-border bg-surface px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
          />
        ) : text.trim() ? (
          <Markdown>{text}</Markdown>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <p className="mb-1 font-medium text-text">No summary yet</p>
            <p className="max-w-sm">
              Generate a recap of the meeting — attendees, decisions, action
              items, open questions and risks
              {canPost ? (
                <>
                  {" "}
                  — then post it into <strong>{topicTitle ?? "the topic"}</strong>.
                </>
              ) : (
                "."
              )}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
