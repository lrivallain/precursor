import { useMemo, useState } from "react";
import { Radio, Trash2 } from "lucide-react";
import type { MeetingSession, TopicNode } from "../lib/types";
import { api } from "../lib/api";
import { useConfirm } from "./ConfirmDialog";

interface TopicLookup {
  id: number;
  title: string;
}

function flattenTopics(tree: TopicNode[]): TopicLookup[] {
  const out: TopicLookup[] = [];
  const walk = (nodes: TopicNode[]): void => {
    for (const n of nodes) {
      out.push({ id: n.id, title: n.title });
      if (n.children.length) walk(n.children);
    }
  };
  walk(tree);
  return out;
}

interface LiveViewProps {
  session: MeetingSession;
  topics: TopicNode[];
  onUpdated: (session: MeetingSession) => void;
  onDeleted: () => void | Promise<void>;
}

/**
 * Live meeting session view. Phase 1 renders the session's metadata and
 * lifecycle controls (end / reopen / delete); the transcript, live insights,
 * and Q&A surfaces arrive in later phases.
 */
export function LiveView({ session, topics, onUpdated, onDeleted }: LiveViewProps) {
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState(false);

  const topicTitle = useMemo(() => {
    if (session.topic_id == null) return null;
    return flattenTopics(topics).find((t) => t.id === session.topic_id)?.title ?? null;
  }, [topics, session.topic_id]);

  const isEnded = session.status === "ended";

  async function setStatus(status: "active" | "ended"): Promise<void> {
    if (busy) return;
    setBusy(true);
    try {
      const updated = await api.updateMeetingSession(session.id, { status });
      onUpdated(updated);
    } finally {
      setBusy(false);
    }
  }

  async function remove(): Promise<void> {
    const ok = await confirmAction({
      variant: "danger",
      title: "Delete session",
      message: `Delete “${session.title}” and its transcript? This can't be undone.`,
      confirmLabel: "Delete",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteMeetingSession(session.id);
      await onDeleted();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-border px-4 py-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted">
          <span
            className={`inline-flex items-center gap-1 ${
              isEnded ? "text-muted" : "text-accent"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${isEnded ? "bg-muted" : "bg-accent"}`}
            />
            {isEnded ? "Ended" : "Active"}
          </span>
          <span>Language: {session.language || "default"}</span>
          <span>Topic: {topicTitle ?? "none"}</span>
        </div>
        <div className="flex items-center gap-2">
          {isEnded ? (
            <button
              type="button"
              onClick={() => void setStatus("active")}
              disabled={busy}
              className="rounded border border-border px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-60"
            >
              Reopen
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void setStatus("ended")}
              disabled={busy}
              className="rounded border border-border px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-60"
            >
              End session
            </button>
          )}
          <button
            type="button"
            onClick={() => void remove()}
            disabled={busy}
            aria-label="Delete session"
            data-tooltip="Delete session"
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-red-500 disabled:opacity-60"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      <div className="flex flex-1 items-center justify-center p-8 text-center">
        <div className="max-w-md text-sm text-muted">
          <Radio size={20} className="mx-auto mb-2 opacity-70" aria-hidden="true" />
          <p className="mb-1 font-medium text-text">Transcription coming next</p>
          <p>
            Live capture, the diarized transcript, real-time insights, and the
            summary-to-topic flow arrive in the following updates. This session
            is saved and ready.
          </p>
        </div>
      </div>
    </div>
  );
}
