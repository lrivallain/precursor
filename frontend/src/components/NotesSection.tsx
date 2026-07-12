import { Check, Eye, Loader2, Pencil } from "lucide-react";
import { useState } from "react";
import { Markdown } from "./Markdown";

interface Props {
  text: string;
  setText: (text: string) => void;
  /** True while a debounced save is in flight. */
  saving: boolean;
  /** True once the latest edits have been persisted. */
  saved: boolean;
}

/**
 * Live meeting notes: a Markdown scratch pad with edit/preview modes. The text
 * is owned by the parent (LiveView) so switching tabs never drops in-progress
 * content; edits autosave (debounced) and on session end.
 */
export function NotesSection({ text, setText, saving, saved }: Props) {
  const [mode, setMode] = useState<"edit" | "preview">("edit");

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Notes</div>
        <div className="ml-1 flex items-center gap-0.5 text-xs">
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
        <div className="ml-auto flex items-center gap-1 text-[11px] text-muted">
          {saving ? (
            <>
              <Loader2 size={11} className="animate-spin" /> Saving…
            </>
          ) : saved ? (
            <>
              <Check size={11} /> Saved
            </>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {mode === "edit" ? (
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Jot down notes as the meeting goes… (Markdown supported)"
            className="h-full min-h-[12rem] w-full resize-none rounded border border-border bg-surface px-3 py-2 font-mono text-[13px] outline-none focus:border-accent"
          />
        ) : text.trim() ? (
          <Markdown>{text}</Markdown>
        ) : (
          <p className="text-sm text-muted">Nothing to preview yet.</p>
        )}
      </div>
    </div>
  );
}
