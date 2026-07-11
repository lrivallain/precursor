import { useState } from "react";
import { Check, Copy, FileText, Send } from "lucide-react";
import { CommandPanel } from "./CommandPanel";

interface Props {
  /** Header + window title. */
  title: string;
  /** True while the initial summary is being generated (floating host only). */
  generating?: boolean;
  /** Generation error, if any. */
  genError?: string | null;
  text: string;
  onTextChange: (text: string) => void;
  /** Whether a topic is linked (gates the Post action). */
  canPost: boolean;
  topicTitle: string | null;
  /** Append the summary to the linked topic. Throws on failure. */
  onPost: (text: string) => Promise<void>;
  onClose: () => void;
  variant?: "floating" | "embedded";
  /** Floating variant only — hand the current body off to a detached window. */
  onPopOut?: (body: string) => void;
  windowStorageKey: string;
}

/**
 * Meeting-summary editor built on the shared {@link CommandPanel} shell, so it
 * behaves exactly like the Notes / GitHub draft panels: a floating, draggable,
 * resizable window with Edit/Preview, a pop-out control (detach into its own OS
 * window), and a footer of actions (Copy, Post to topic).
 */
export function SummaryPanel({
  title,
  generating = false,
  genError = null,
  text,
  onTextChange,
  canPost,
  topicTitle,
  onPost,
  onClose,
  variant = "floating",
  onPopOut,
  windowStorageKey,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [posting, setPosting] = useState(false);
  const [posted, setPosted] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);

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
      await onPost(text);
      setPosted(true);
      setTimeout(onClose, 900);
    } catch (e) {
      setPostError(e instanceof Error ? e.message : "Couldn't post to the topic.");
    } finally {
      setPosting(false);
    }
  }

  const footer = (
    <>
      <span className="mr-auto min-w-0 truncate text-[11px] text-muted">
        {canPost ? (
          <>
            Posts into <strong>{topicTitle ?? "the topic"}</strong>.
          </>
        ) : (
          "Attach a topic to post the summary."
        )}
      </span>
      <button
        type="button"
        onClick={() => void copy()}
        disabled={!text}
        className="inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1.5 text-sm hover:bg-bg disabled:opacity-50"
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? "Copied" : "Copy"}
      </button>
      <button
        type="button"
        onClick={() => void post()}
        disabled={!text || posting || !canPost || posted}
        className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-50"
      >
        {posted ? <Check size={14} /> : <Send size={14} />}
        {posted ? "Posted" : posting ? "Posting…" : "Post to topic"}
      </button>
    </>
  );

  return (
    <CommandPanel
      icon={<FileText size={15} />}
      title={title}
      onClose={onClose}
      loading={generating}
      loadingLabel="Generating summary…"
      body={text}
      onBodyChange={onTextChange}
      bodyPlaceholder="The generated summary appears here…"
      previewEmptyHint="Nothing to preview yet."
      windowStorageKey={windowStorageKey}
      error={postError ?? genError}
      footer={footer}
      variant={variant}
      onPopOut={onPopOut ? (snap) => onPopOut(snap.body) : undefined}
    />
  );
}
