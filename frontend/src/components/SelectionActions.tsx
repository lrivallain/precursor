import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NotebookPen, PenLine, Split } from "lucide-react";
import { markdownSourceRange } from "../lib/markdownCaret";
import type { ReplyEditSelection } from "../lib/useReplyEdit";

interface Picked {
  messageId: number;
  text: string;
  /** The selection's Markdown source in the reply, when it could be mapped. */
  range: ReplyEditSelection | null;
  markdown: string;
  x: number;
  y: number;
}

/** The reply (see MessageBubble's `quotableId`) a DOM node sits in. */
function replyOf(node: Node | null): HTMLElement | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null);
  return el?.closest<HTMLElement>("[data-reply-id]") ?? null;
}

/**
 * Floating actions over text selected inside one reply: start a side chat
 * quoting it, edit the reply with the selection pre-selected, or copy its
 * Markdown into the notes pad. An action left undefined is hidden.
 */
export function SelectionActions({
  scrollRef,
  sourceOf,
  onSideChat,
  onEdit,
  onNote,
}: {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  /** The Markdown source of a reply, to map the selection back to it. */
  sourceOf: (messageId: number) => string | undefined;
  onSideChat?: (messageId: number, quote: string) => void;
  onEdit?: (messageId: number, range: ReplyEditSelection | null) => void;
  onNote?: (markdown: string) => void;
}) {
  const [picked, setPicked] = useState<Picked | null>(null);
  const [width, setWidth] = useState(0);
  const barRef = useRef<HTMLDivElement>(null);
  // Read at selection time, so a new transcript doesn't re-bind the listeners.
  const sourceRef = useRef(sourceOf);
  sourceRef.current = sourceOf;

  useEffect(() => {
    const box = scrollRef.current;
    if (!box) return;
    const read = () => {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return setPicked(null);
      const range = sel.getRangeAt(0);
      const reply = replyOf(range.startContainer);
      if (!reply || reply !== replyOf(range.endContainer) || !box.contains(reply)) {
        return setPicked(null);
      }
      const text = sel.toString().trim();
      if (text.length < 2) return setPicked(null);
      const messageId = Number(reply.dataset.replyId);
      const source = sourceRef.current(messageId);
      const mapped = source != null ? markdownSourceRange(reply, range, source) : null;
      const exact = source != null
        ? markdownSourceRange(reply, range, source, { wholeLines: false })
        : null;
      const rect = range.getBoundingClientRect();
      setPicked({
        messageId,
        text,
        range: exact,
        markdown: mapped && source != null ? source.slice(mapped.start, mapped.end).trim() : text,
        x: rect.left + rect.width / 2,
        y: rect.top,
      });
    };
    // After the browser settles the selection the gesture made.
    const later = () => window.setTimeout(read, 0);
    const hide = () => setPicked(null);
    document.addEventListener("mouseup", later);
    document.addEventListener("keyup", later);
    box.addEventListener("scroll", hide, { passive: true });
    return () => {
      document.removeEventListener("mouseup", later);
      document.removeEventListener("keyup", later);
      box.removeEventListener("scroll", hide);
    };
  }, [scrollRef]);

  // Centre on whole pixels: a translate(-50%) at a fractional offset blurs text.
  useLayoutEffect(() => {
    if (barRef.current) setWidth(barRef.current.offsetWidth);
  }, [picked, onSideChat, onEdit, onNote]);

  if (!picked || (!onSideChat && !onEdit && !onNote)) return null;
  const left = Math.max(8, Math.round(picked.x - width / 2));
  const top = Math.max(8, Math.round(picked.y - 36));
  const run = (action: () => void) => {
    action();
    window.getSelection()?.removeAllRanges();
    setPicked(null);
  };
  // It floats over the transcript, so no state may use a translucent fill.
  return createPortal(
    <div
      ref={barRef}
      role="toolbar"
      aria-label="Selection actions"
      // Keep the selection: a mousedown elsewhere would collapse it first.
      onMouseDown={(e) => e.preventDefault()}
      className="fixed z-40 flex items-center divide-x divide-border overflow-hidden rounded-full border border-accent/40 bg-surface text-xs font-medium text-accent shadow-md"
      style={{ left, top, visibility: width ? undefined : "hidden" }}
    >
      {onSideChat && (
        <Action
          icon={<Split size={12} />}
          label="Side chat"
          tooltip="Start a side chat quoting only the selected text"
          onClick={() => run(() => onSideChat(picked.messageId, picked.text))}
        />
      )}
      {onEdit && (
        <Action
          icon={<PenLine size={12} />}
          label="Edit"
          tooltip={"Edit this reply, starting at the selection\nLater turns use your version"}
          onClick={() => run(() => onEdit(picked.messageId, picked.range))}
        />
      )}
      {onNote && (
        <Action
          icon={<NotebookPen size={12} />}
          label="Note"
          tooltip="Add the selection's Markdown to the notes pad"
          onClick={() => run(() => onNote(picked.markdown))}
        />
      )}
    </div>,
    document.body,
  );
}

function Action({
  icon,
  label,
  tooltip,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  tooltip: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1.5 px-2.5 py-1 hover:bg-accent hover:text-white"
      data-tooltip={tooltip}
    >
      {icon}
      {label}
    </button>
  );
}
