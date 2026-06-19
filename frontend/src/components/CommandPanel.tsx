import { type ClipboardEventHandler, type ReactNode, useState } from "react";
import { createPortal } from "react-dom";
import { Eye, Loader2, Pencil, X } from "lucide-react";
import { Markdown } from "./Markdown";
import { useFloatingWindow } from "../lib/useFloatingWindow";

interface TitleField {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  placeholder?: string;
}

interface Props {
  /** Leading header icon (already sized, e.g. a lucide icon at size 14). */
  icon: ReactNode;
  title: string;
  subtitle?: string;
  onClose: () => void;
  closeLabel?: string;
  /** Shows a spinner + label in the body instead of the editor. */
  loading?: boolean;
  loadingLabel?: string;
  /** Optional single-line title input shown above the body in edit mode. */
  titleField?: TitleField;
  body: string;
  onBodyChange: (value: string) => void;
  onBodyPaste?: ClipboardEventHandler<HTMLTextAreaElement>;
  bodyPlaceholder?: string;
  bodyTop?: ReactNode;
  previewBottom?: ReactNode;
  /** Extra classes for the textarea (e.g. ``font-mono``). */
  bodyClassName?: string;
  /** Distinct key so each usage remembers its own window position + size. */
  windowStorageKey: string;
  /** Shown in preview mode when the body is empty. */
  previewEmptyHint?: string;
  /** Disables editing (e.g. while an action is in flight). */
  disabled?: boolean;
  error?: string | null;
  /** Action buttons rendered in the footer bar. */
  footer?: ReactNode;
}

type Mode = "edit" | "preview";

/**
 * Shared shell for the build-in command panels (notes + GitHub drafts).
 *
 * Renders as a **floating window** (portal to ``document.body``): draggable by
 * its header and resizable from the bottom-right grip, with position + size
 * persisted per ``windowStorageKey``. Detaching it from the chat layout means
 * the scratch pad / draft cards no longer share vertical space with the message
 * composer — each is sized independently. Provides a consistent header (icon,
 * title, Edit/Preview toggle, close), a body that flexes to fill the window
 * (Markdown editor with an optional title field, or a live preview), and a
 * footer slot for each command's own action buttons.
 */
export function CommandPanel({
  icon,
  title,
  subtitle,
  onClose,
  closeLabel = "Close",
  loading = false,
  loadingLabel = "Drafting from recent discussion…",
  titleField,
  body,
  onBodyChange,
  onBodyPaste,
  bodyPlaceholder = "Write in GitHub-Flavored Markdown…",
  bodyTop,
  previewBottom,
  bodyClassName = "",
  windowStorageKey,
  previewEmptyHint = "Nothing to preview.",
  disabled = false,
  error,
  footer,
}: Props) {
  const [mode, setMode] = useState<Mode>("edit");
  const { style, onDragStart, onResizeStart } = useFloatingWindow({
    storageKey: windowStorageKey,
    defaultWidth: 580,
    defaultHeight: 440,
    minWidth: 360,
    minHeight: 260,
  });
  const showPreviewTitle = titleField !== undefined && titleField.value.trim().length > 0;

  return createPortal(
    <div
      style={style}
      className="z-50 flex flex-col border border-border rounded-lg bg-surface shadow-2xl"
    >
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <div
          onMouseDown={onDragStart}
          title="Drag to move"
          className="flex items-center gap-2 flex-1 min-w-0 cursor-move select-none"
        >
          <span className="text-muted shrink-0">{icon}</span>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">{title}</div>
            {subtitle && <div className="text-[11px] text-muted truncate">{subtitle}</div>}
          </div>
        </div>
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded ${
              mode === "edit" ? "bg-bg text-text" : "text-muted hover:text-text"
            }`}
            aria-pressed={mode === "edit"}
          >
            <Pencil size={11} /> Edit
          </button>
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded ${
              mode === "preview" ? "bg-bg text-text" : "text-muted hover:text-text"
            }`}
            aria-pressed={mode === "preview"}
          >
            <Eye size={11} /> Preview
          </button>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded text-muted hover:bg-bg"
          aria-label={closeLabel}
          data-tooltip={closeLabel}
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 min-h-0 flex flex-col gap-2 p-3">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" />
            {loadingLabel}
          </div>
        ) : mode === "edit" ? (
          <>
            {bodyTop}
            {titleField && (
              <label className="block">
                <span className="block text-[11px] text-muted mb-1">
                  {titleField.label ?? "Title"}
                </span>
                <input
                  type="text"
                  value={titleField.value}
                  onChange={(e) => titleField.onChange(e.target.value)}
                  disabled={disabled}
                  placeholder={titleField.placeholder}
                  className="w-full bg-bg border border-border rounded p-2 text-sm outline-none focus:border-accent disabled:opacity-60"
                />
              </label>
            )}
            <textarea
              value={body}
              onChange={(e) => onBodyChange(e.target.value)}
              onPaste={onBodyPaste}
              placeholder={bodyPlaceholder}
              disabled={disabled}
              aria-label={`${title} body`}
              className={`flex-1 min-h-0 w-full resize-none bg-bg border border-border rounded p-2 text-sm outline-none focus:border-accent disabled:opacity-60 ${bodyClassName}`}
            />
          </>
        ) : (
          <div className="flex-1 min-h-0 overflow-auto text-sm leading-relaxed bg-bg border border-border rounded p-3">
            {showPreviewTitle && (
              <h3 className="!mt-0 !mb-2 text-base font-semibold">{titleField.value.trim()}</h3>
            )}
            {body.trim() ? (
              <Markdown>{body}</Markdown>
            ) : (
              <span className="text-muted italic">{previewEmptyHint}</span>
            )}
            {previewBottom}
          </div>
        )}
      </div>

      {error && <div className="px-3 pb-2 text-xs text-red-500 break-words">{error}</div>}

      {footer && (
        <div className="flex flex-wrap items-center justify-end gap-2 px-3 py-2 border-t border-border">
          {footer}
        </div>
      )}

      <div
        onMouseDown={onResizeStart}
        title="Drag to resize"
        aria-hidden="true"
        className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize text-muted/70 hover:text-accent"
      >
        <svg viewBox="0 0 10 10" className="h-full w-full" fill="none" stroke="currentColor">
          <path d="M9 3 L3 9 M9 6 L6 9" strokeWidth="1" />
        </svg>
      </div>
    </div>,
    document.body,
  );
}
