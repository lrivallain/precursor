import { useEffect, useRef, useState } from "react";
import { Loader2, MessageSquarePlus, NotebookPen, Save, Send, Sparkles } from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { CommandPanel } from "./CommandPanel";

export type NotesAction =
  | "append"
  | "append-and-ask"
  | "post-comment";

interface Props {
  /** True when the topic is linked to a GitHub issue (controls the post button). */
  hasIssue: boolean;
  /** When false, the "Post as comment" (GitHub) action is hidden entirely (chats). */
  allowPostComment?: boolean;
  /** True while a rephrase round-trip is in flight. */
  rephrasing: boolean;
  /** True while one of the terminal actions is in flight. */
  acting: boolean;
  /** True while loading a previously saved draft. */
  loadingDraft?: boolean;
  /** True while persisting the current draft. */
  savingDraft?: boolean;
  error: string | null;
  onRephrase: (text: string) => void | Promise<void>;
  onSaveDraft?: (text: string) => void | Promise<void>;
  onAction: (action: NotesAction, text: string) => void | Promise<void>;
  onCancel: (text: string) => void | Promise<void>;
  /** Seed text loaded from the server-side draft store. */
  initialText?: string;
  /**
   * When the parent receives a rephrased text, it pushes it back here so the
   * textarea is updated in place. We use a controlled-ish pattern: the parent
   * owns the seed, this component owns the live edits.
   */
  rephrasedText?: string;
}

export function NotesPanel({
  hasIssue,
  allowPostComment = true,
  rephrasing,
  acting,
  loadingDraft = false,
  savingDraft = false,
  error,
  onRephrase,
  onSaveDraft,
  onAction,
  onCancel,
  initialText,
  rephrasedText,
}: Props) {
  const [text, setText] = useState(initialText ?? "");

  useEffect(() => {
    setText(initialText ?? "");
  }, [initialText]);

  // Apply an AI rephrase result exactly when a rephrase round-trip finishes
  // (rephrasing: true → false), not on every render. Keying on that lifecycle
  // instead of comparing against the live text means the user can freely edit
  // the suggestion afterwards without their keystrokes snapping back to the
  // AI version on the next render.
  const prevRephrasingRef = useRef(rephrasing);
  useEffect(() => {
    const justFinished = prevRephrasingRef.current && !rephrasing;
    prevRephrasingRef.current = rephrasing;
    if (justFinished && !error && rephrasedText !== undefined) {
      setText(rephrasedText);
    }
  }, [rephrasing, error, rephrasedText]);

  const empty = !text.trim();
  const busy = rephrasing || acting || loadingDraft || savingDraft;

  return (
    <CommandPanel
      icon={<NotebookPen size={14} className="text-accent" />}
      title="Notes"
      subtitle="scratch pad — not posted until you choose an action"
      onClose={() => void onCancel(text)}
      closeLabel="Discard notes"
      body={text}
      onBodyChange={setText}
      bodyPlaceholder="Start typing your notes… (Markdown supported)"
      windowStorageKey="precursor:notesPanel:window"
      previewEmptyHint="Nothing to preview yet."
      disabled={busy}
      error={error}
      footer={
        <>
          <button
            onClick={() => void onRephrase(text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
          >
            {rephrasing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {rephrasing ? "Rephrasing…" : "Rephrase with AI"}
          </button>
          {onSaveDraft && (
            <button
              onClick={() => void onSaveDraft(text)}
              disabled={empty || busy}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            >
              {savingDraft ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              {savingDraft ? "Saving…" : "Save draft"}
            </button>
          )}

          <div className="flex-1" />

          <button
            onClick={() => void onAction("append", text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            title="Append the notes to the conversation without invoking the assistant."
          >
            <MessageSquarePlus size={14} />
            Add to chat
          </button>
          <button
            onClick={() => void onAction("append-and-ask", text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
            title="Append the notes and ask the assistant to comment on them."
          >
            <Send size={14} />
            Add &amp; ask AI
          </button>
          {allowPostComment && (
            <button
              onClick={() => void onAction("post-comment", text)}
              disabled={empty || busy || !hasIssue}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-accent text-white text-xs disabled:opacity-40"
              title={
                hasIssue
                  ? "Post the notes as a comment on the linked GitHub issue."
                  : "Link a GitHub issue to this topic to enable posting."
              }
            >
              <Github size={14} />
              Post as comment
            </button>
          )}
        </>
      }
    />
  );
}
