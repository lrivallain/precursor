import { useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";

// Shell state the window-level shortcuts drive. Both setters are React state
// setters, so they're stable: each listener registers exactly when it did
// inline in App — the drawer's only while it's open, the palette's once.
export interface GlobalShortcutsDeps {
  mobileNavOpen: boolean;
  setMobileNavOpen: Dispatch<SetStateAction<boolean>>;
  setPaletteOpen: Dispatch<SetStateAction<boolean>>;
}

// Bare-key shortcuts (like "/") must never steal a keystroke the user meant to
// type, so they're ignored while focus sits in any editable control — including
// contenteditable surfaces (the composer's rich editors) and shadow-DOM inputs
// reported via composedPath().
function isTypingTarget(e: KeyboardEvent): boolean {
  const path = typeof e.composedPath === "function" ? e.composedPath() : [e.target];
  for (const node of path) {
    if (!(node instanceof HTMLElement)) continue;
    const tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (node.isContentEditable) return true;
  }
  return false;
}

// Kept as two listeners rather than one: the drawer's Escape handler is only
// attached while the drawer is open, so folding it into the palette's
// always-on listener would change when (and in which order) it's registered.
export function useGlobalShortcuts(deps: GlobalShortcutsDeps): void {
  const { mobileNavOpen, setMobileNavOpen, setPaletteOpen } = deps;

  useEffect(() => {
    if (!mobileNavOpen) return;
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") setMobileNavOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavOpen]);

  // Global ⌘K / Ctrl+K toggles the command palette — a width-independent way to
  // jump to any section regardless of the sidebar's horizontal overflow. A bare
  // "/" opens it too (search-first, like GitHub), but only when the user isn't
  // typing and no other dialog owns the screen.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (isTypingTarget(e)) return;
        // Any open modal (including the palette itself) keeps the key.
        if (document.querySelector('[aria-modal="true"]')) return;
        e.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
