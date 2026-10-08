import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { RefineTextarea } from "./RefineTextarea";
import { focusTextareaAt } from "../lib/markdownCaret";
import type { ReplyEditSelection } from "../lib/useReplyEdit";

interface Props {
  content: string;
  /** Source range to select on open (the text the user had selected). */
  selection: ReplyEditSelection | null;
  saving: boolean;
  error: string | null;
  onSave: (content: string) => void;
  onCancel: () => void;
}

/** In-place Markdown editor for an assistant reply. */
export function ReplyEditor({ content, selection, saving, error, onSave, onCancel }: Props) {
  const [text, setText] = useState(content);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the reply so long answers edit without a nested scrollbar.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight + 2, window.innerHeight * 0.7)}px`;
  }, [text]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (selection) {
      focusTextareaAt(el, selection.start);
      el.setSelectionRange(selection.start, selection.end);
    } else {
      focusTextareaAt(el, el.value.length);
    }
    // Only on open: later selection changes belong to the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changed = text !== content && text.trim().length > 0;
  return (
    <div className="flex min-w-[min(36rem,80vw)] flex-col gap-1.5">
      <RefineTextarea
        ref={ref}
        value={text}
        onValueChange={setText}
        markdown
        disabled={saving}
        aria-label="Edit reply"
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
          } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && changed) {
            e.preventDefault();
            onSave(text);
          }
        }}
        className="w-full resize-y rounded border border-border bg-bg px-2.5 py-2 font-mono text-[13px] leading-relaxed outline-none focus:border-accent"
      />
      {error && <div className="text-xs text-red-500">{error}</div>}
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto text-[11px] text-muted">
          Later turns use your edit. The original stays restorable.
        </span>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded border border-border px-2.5 py-1 text-xs hover:bg-bg"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onSave(text)}
          disabled={!changed || saving}
          className="inline-flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-50"
          data-tooltip="Save the edit (⌘/Ctrl+Enter)"
        >
          {saving && <Loader2 size={12} className="animate-spin" />}
          Save
        </button>
      </div>
    </div>
  );
}
