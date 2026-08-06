import {
  Bold,
  Code,
  Heading,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Quote,
  Strikethrough,
} from "lucide-react";
import type { ComponentType } from "react";

import type { MarkdownAction } from "../lib/markdownFormat";

interface ToolbarItem {
  action: MarkdownAction;
  icon: ComponentType<{ size?: number | string }>;
  /** Tooltip label; the shortcut hint (if any) is appended in parentheses. */
  label: string;
  shortcut?: string;
}

// Shortcut hints mirror the app's other tooltips, which show ⌘ regardless of
// platform (the handler in useMarkdownEditor accepts ⌘ or Ctrl either way).
const ITEMS: ToolbarItem[] = [
  { action: "bold", icon: Bold, label: "Bold", shortcut: "⌘B" },
  { action: "italic", icon: Italic, label: "Italic", shortcut: "⌘I" },
  { action: "strikethrough", icon: Strikethrough, label: "Strikethrough", shortcut: "⌘⇧X" },
  { action: "code", icon: Code, label: "Code", shortcut: "⌘E" },
  { action: "link", icon: LinkIcon, label: "Link", shortcut: "⌘K" },
  { action: "heading", icon: Heading, label: "Heading" },
  { action: "quote", icon: Quote, label: "Quote" },
  { action: "unordered-list", icon: List, label: "Bulleted list" },
  { action: "ordered-list", icon: ListOrdered, label: "Numbered list" },
];

export interface MarkdownToolbarProps {
  onFormat: (action: MarkdownAction) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * A compact row of formatting buttons for a Markdown textarea. It only emits
 * intents via `onFormat`; the owning editor mutates the text and moves the
 * caret. `onMouseDown` is prevented so clicking a button never steals focus
 * (and thus the selection) from the textarea.
 */
export function MarkdownToolbar({ onFormat, disabled, className }: MarkdownToolbarProps) {
  return (
    <div
      role="toolbar"
      aria-label="Formatting"
      className={`flex flex-wrap items-center gap-0.5 ${className ?? ""}`}
    >
      {ITEMS.map(({ action, icon: Icon, label, shortcut }) => (
        <button
          key={action}
          type="button"
          disabled={disabled}
          data-tooltip={shortcut ? `${label} (${shortcut})` : label}
          aria-label={label}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onFormat(action)}
          className="inline-flex h-6 w-6 items-center justify-center rounded text-muted transition-colors hover:bg-surface hover:text-text disabled:pointer-events-none disabled:opacity-40"
        >
          <Icon size={14} />
        </button>
      ))}
    </div>
  );
}
