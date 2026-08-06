import { useCallback, type KeyboardEvent, type RefObject } from "react";

import { applyMarkdown, type MarkdownAction } from "./markdownFormat";

export interface UseMarkdownEditor {
  /** Apply a formatting action to the current selection of the textarea. */
  applyFormat: (action: MarkdownAction) => void;
  /**
   * Keyboard handler for the textarea. Returns true when it consumed the event
   * (a formatting shortcut fired) so the caller can skip its own handling.
   */
  handleKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => boolean;
}

/** ⌘/Ctrl + key → formatting action. ⇧ is required only for strikethrough. */
function actionForKey(e: KeyboardEvent<HTMLTextAreaElement>): MarkdownAction | null {
  if (!(e.metaKey || e.ctrlKey) || e.altKey) return null;
  const key = e.key.toLowerCase();
  if (e.shiftKey) return key === "x" ? "strikethrough" : null;
  switch (key) {
    case "b":
      return "bold";
    case "i":
      return "italic";
    case "k":
      return "link";
    case "e":
      return "code";
    default:
      return null;
  }
}

/**
 * Wires a controlled textarea up to the Markdown transforms: `applyFormat` (for
 * toolbar buttons) and `handleKeyDown` (for ⌘/Ctrl shortcuts). Selection is read
 * from — and written back to — the live DOM node so it never fights a stale
 * React value, and the textarea is refocused after each edit.
 */
export function useMarkdownEditor(
  ref: RefObject<HTMLTextAreaElement | null>,
  onValueChange: (value: string) => void,
): UseMarkdownEditor {
  const applyFormat = useCallback(
    (action: MarkdownAction) => {
      const el = ref.current;
      if (!el) return;
      const next = applyMarkdown(action, {
        value: el.value,
        selectionStart: el.selectionStart ?? el.value.length,
        selectionEnd: el.selectionEnd ?? el.value.length,
      });
      onValueChange(next.value);
      // The value prop updates on the next render; restore the caret after it
      // lands so the browser doesn't reset it to the end of the field.
      requestAnimationFrame(() => {
        el.focus();
        el.setSelectionRange(next.selectionStart, next.selectionEnd);
      });
    },
    [ref, onValueChange],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
      const action = actionForKey(e);
      if (!action) return false;
      e.preventDefault();
      // Keep the shortcut local — otherwise ⌘K would bubble to the global
      // command-palette toggle instead of inserting a link.
      e.stopPropagation();
      applyFormat(action);
      return true;
    },
    [applyFormat],
  );

  return { applyFormat, handleKeyDown };
}
