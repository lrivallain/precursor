import { useState } from "react";
import {
  Loader2,
  MessageSquarePlus,
  NotebookPen,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { GithubIcon as Github } from "./icons/GithubIcon";
import { ResizableTextarea } from "./ResizableTextarea";

export type NotesAction =
  | "append"
  | "append-and-ask"
  | "post-comment";

interface Props {
  /** True when the topic is linked to a GitHub issue (controls the post button). */
  hasIssue: boolean;
  /** True while a rephrase round-trip is in flight. */
  rephrasing: boolean;
  /** True while one of the terminal actions is in flight. */
  acting: boolean;
  error: string | null;
  onRephrase: (text: string) => void | Promise<void>;
  onAction: (action: NotesAction, text: string) => void | Promise<void>;
  onCancel: () => void;
  /**
   * When the parent receives a rephrased text, it pushes it back here so the
   * textarea is updated in place. We use a controlled-ish pattern: the parent
   * owns the seed, this component owns the live edits.
   */
  rephrasedText?: string;
}

export function NotesPanel({
  hasIssue,
  rephrasing,
  acting,
  error,
  onRephrase,
  onAction,
  onCancel,
  rephrasedText,
}: Props) {
  const [text, setText] = useState("");

  // When the parent gives us a rebuilt version, swap it in.
  if (rephrasedText !== undefined && rephrasedText !== text && !acting) {
    // Setting state during render is fine here because the check above
    // breaks the loop on the next render.
    setText(rephrasedText);
  }

  const empty = !text.trim();
  const busy = rephrasing || acting;

  return (
    <div className="border border-border rounded bg-surface">
      <header className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <NotebookPen size={14} className="text-accent" />
          <span className="text-sm font-medium truncate">Notes</span>
          <span className="text-[11px] text-muted truncate">
            scratch pad — not posted until you choose an action
          </span>
        </div>
        <button
          onClick={onCancel}
          className="p-1 rounded hover:bg-bg text-muted"
          aria-label="Discard notes"
          data-tooltip="Discard notes"
        >
          <X size={14} />
        </button>
      </header>

      <div className="p-3 space-y-2">
        <ResizableTextarea
          value={text}
          onChange={setText}
          storageKey="precursor:notesPanel:height"
          placeholder="Start typing your notes… (Markdown supported)"
          disabled={busy}
          aria-label="Notes"
        />

        {error && (
          <div className="text-xs text-red-500 whitespace-pre-wrap break-words">
            {error}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 justify-end">
          <button
            onClick={() => void onRephrase(text)}
            disabled={empty || busy}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded border border-border text-xs hover:bg-bg disabled:opacity-40"
          >
            {rephrasing ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {rephrasing ? "Rephrasing…" : "Rephrase with AI"}
          </button>

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
        </div>
      </div>
    </div>
  );
}
