export interface ShortcutEntry {
  /** One key cap per element; rendered as separate `<kbd>`s. */
  keys: string[];
  description: string;
}

export interface ShortcutGroup {
  title: string;
  entries: ShortcutEntry[];
}

export const IS_MAC =
  typeof navigator !== "undefined" && /mac|iphone|ipad/i.test(navigator.platform);

const MOD = IS_MAC ? "⌘" : "Ctrl";
const SHIFT = IS_MAC ? "⇧" : "Shift";

// Source of truth for the "Keyboard shortcuts" dialog. The handlers live in
// useGlobalShortcuts.ts and Composer.tsx; keep this list in step with them.
export const SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: "Anywhere",
    entries: [
      { keys: [MOD, "K"], description: "Open the command palette" },
      { keys: ["/"], description: "Open the command palette (when not typing)" },
      { keys: [MOD, SHIFT, "K"], description: "Ask IQ, using the selected text" },
      { keys: ["C"], description: "Focus the message composer (when not typing)" },
      { keys: ["N"], description: "Start a new item in the current section (when not typing)" },
      { keys: ["?"], description: "Show this list (when not typing)" },
      { keys: ["Esc"], description: "Close the open dialog or menu" },
    ],
  },
  {
    title: "Composer",
    entries: [
      { keys: ["Enter"], description: "Send the message" },
      { keys: ["Shift", "Enter"], description: "Insert a newline" },
      { keys: ["↑", "↓"], description: "Recall earlier messages" },
      { keys: ["Esc"], description: "Stop the reply that is streaming" },
    ],
  },
];
