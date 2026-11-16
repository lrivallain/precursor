import { useEffect, useRef } from "react";

type KeyLike = Pick<KeyboardEvent, "key" | "metaKey" | "ctrlKey" | "altKey" | "shiftKey">;

/** Cmd+S on macOS, Ctrl+S elsewhere (either is accepted, as Monaco does). */
export function isSaveShortcut(e: KeyLike): boolean {
  return (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "s";
}

/**
 * Catch Cmd/Ctrl+S anywhere on the page while `enabled`, so the browser's
 * "Save page" dialog never opens over an editor, whatever has focus (a
 * preview, the toolbar, the tree). An editor that already handled the keys
 * itself (Monaco, the textarea fallback) marks the event as handled, and is
 * left alone so the file isn't written twice. Events inside an iframe (the
 * draw.io editor) never reach this window; that editor reports its own saves.
 */
export function useSaveShortcut(enabled: boolean, onSave: () => void): void {
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.defaultPrevented || !isSaveShortcut(e)) return;
      e.preventDefault();
      onSaveRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
