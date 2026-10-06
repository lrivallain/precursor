import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Split } from "lucide-react";

interface Picked {
  messageId: number;
  text: string;
  x: number;
  y: number;
}

/** The reply (see MessageBubble's `quotableId`) a DOM node sits in. */
function replyOf(node: Node | null): HTMLElement | null {
  const el = node instanceof Element ? node : (node?.parentElement ?? null);
  return el?.closest<HTMLElement>("[data-reply-id]") ?? null;
}

/**
 * Floating "Side chat from selection" button over text selected inside one
 * topic reply. Starting from it quotes only the selection, which gives the
 * side chat sharper grounding than the whole reply.
 */
export function SelectionSideChat({
  scrollRef,
  disabled,
  onStart,
}: {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  disabled?: boolean;
  onStart: (messageId: number, quote: string) => void;
}) {
  const [picked, setPicked] = useState<Picked | null>(null);

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
      const rect = range.getBoundingClientRect();
      setPicked({
        messageId: Number(reply.dataset.replyId),
        text,
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

  if (!picked || disabled) return null;
  // It floats over the transcript, so no state may use a translucent fill.
  return createPortal(
    <button
      type="button"
      // Keep the selection: a mousedown elsewhere would collapse it first.
      onMouseDown={(e) => e.preventDefault()}
      onClick={() => {
        onStart(picked.messageId, picked.text);
        window.getSelection()?.removeAllRanges();
        setPicked(null);
      }}
      className="fixed z-40 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-accent/40 bg-surface px-2.5 py-1 text-xs font-medium text-accent shadow-md hover:border-accent hover:bg-accent hover:text-white"
      style={{ left: picked.x, top: Math.max(8, picked.y - 36) }}
      data-tooltip="Start a side chat quoting only the selected text"
    >
      <Split size={12} />
      Side chat from selection
    </button>,
    document.body,
  );
}
