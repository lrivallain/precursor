import { useEffect, useRef } from "react";
import type { Dispatch, SetStateAction } from "react";

// Shell state the window-level shortcuts drive. Both setters are React state
// setters, so they're stable: each listener registers exactly when it did
// inline in App — the drawer's only while it's open, the palette's once.
export interface GlobalShortcutsDeps {
  mobileNavOpen: boolean;
  setMobileNavOpen: Dispatch<SetStateAction<boolean>>;
  setPaletteOpen: Dispatch<SetStateAction<boolean>>;
  /** ⌘⇧K: open the IQ section, seeding the question with the selected text. */
  onAskIQ: (question: string) => void;
}

// The palette is a modal too, but ⌘⇧K is allowed to replace it.
const OTHER_MODAL = '[aria-modal="true"]:not([aria-labelledby="command-palette-input"])';
// Mirrors the backend's cap on a question (schemas/iq.py IQAskRequest).
const MAX_QUESTION = 4000;

function selectedText(): string {
  return (window.getSelection()?.toString() ?? "").replace(/\s+/g, " ").trim().slice(0, MAX_QUESTION);
}

// Surfaces that take text without being a form control or contenteditable.
// Monaco on Chromium types through the EditContext API into a plain
// `<div role="textbox">`, which none of the checks below would catch.
const TEXT_ROLES = new Set(["textbox", "searchbox", "combobox"]);

// Bare-key shortcuts (like "/") must never steal a keystroke the user meant to
// type, so they're ignored while focus sits in any editable control — including
// contenteditable surfaces (the composer's rich editors), ARIA text boxes, code
// editors, and shadow-DOM inputs reported via composedPath().
function isTypingTarget(e: KeyboardEvent): boolean {
  const path = typeof e.composedPath === "function" ? e.composedPath() : [e.target];
  for (const node of path) {
    if (!(node instanceof HTMLElement)) continue;
    const tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (node.isContentEditable) return true;
    if (TEXT_ROLES.has(node.getAttribute("role") ?? "")) return true;
    // Anywhere in a Monaco editor (a read-only diff included), keys are its own.
    if (node.classList.contains("monaco-editor")) return true;
  }
  return false;
}

// Kept as two listeners rather than one: the drawer's Escape handler is only
// attached while the drawer is open, so folding it into the palette's
// always-on listener would change when (and in which order) it's registered.
export function useGlobalShortcuts(deps: GlobalShortcutsDeps): void {
  const { mobileNavOpen, setMobileNavOpen, setPaletteOpen, onAskIQ } = deps;
  // The listener registers once; read the latest callback through a ref.
  const askRef = useRef(onAskIQ);
  askRef.current = onAskIQ;

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
  // typing and no other dialog owns the screen. ⌘⇧K / Ctrl+Shift+K asks IQ
  // instead, from anywhere — typing included, since it's a chord.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (document.querySelector(OTHER_MODAL)) return;
        setPaletteOpen(false);
        askRef.current(selectedText());
        return;
      }
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
