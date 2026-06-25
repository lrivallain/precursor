import { type ClipboardEventHandler, type ReactNode, useState } from "react";
import { createPortal } from "react-dom";
import { ExternalLink, Eye, Loader2, Pencil, X } from "lucide-react";
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
  /**
   * Layout mode. ``"floating"`` (default) renders the in-tab draggable window
   * portalled to ``document.body``. ``"embedded"`` renders a bare, full-size
   * panel meant to fill a detached browser window (see
   * ``DetachedWindowPortal``), without the drag handle, resize grip, or
   * pop-out control.
   */
  variant?: "floating" | "embedded";
  /**
   * When provided (floating variant only), a pop-out button is shown in the
   * header. It receives the current body + title so the host can hand the draft
   * off to a separate window that survives navigation.
   */
  onPopOut?: (snapshot: { body: string; title?: string }) => void;
}

type Mode = "edit" | "preview";

/**
 * Shared shell for the build-in command panels (notes + GitHub drafts).
 *
 * In the default ``floating`` variant it renders as a draggable, resizable
 * window portalled to ``document.body`` (position + size persisted per
 * ``windowStorageKey``) so the scratch pad / draft cards float over the chat
 * instead of sharing the composer's vertical space. Provides a consistent
 * header (icon, title, Edit/Preview toggle, optional pop-out, close), a body
 * that flexes to fill the window (Markdown editor with an optional title field,
 * or a live preview), and a footer slot for each command's own action buttons.
 *
 * The header's pop-out control (``onPopOut``) hands the draft off to a separate
 * native browser window owned at app level, which keeps working against the
 * original conversation even after the user navigates away. That detached window
 * re-renders this same panel in the ``embedded`` variant.
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
  variant = "floating",
  onPopOut,
}: Props) {
  const embedded = variant === "embedded";
  const [mode, setMode] = useState<Mode>("edit");
  const { style, onDragStart, onResizeStart } = useFloatingWindow({
    storageKey: windowStorageKey,
    defaultWidth: 580,
    defaultHeight: 440,
    minWidth: 360,
    minHeight: 260,
  });
  const showPreviewTitle = titleField !== undefined && titleField.value.trim().length > 0;

  const header = (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
      <div
        onMouseDown={embedded ? undefined : onDragStart}
        title={embedded ? undefined : "Drag to move"}
        className={`flex items-center gap-2 flex-1 min-w-0 select-none ${
          embedded ? "" : "cursor-move"
        }`}
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
      {!embedded && onPopOut && (
        <button
          type="button"
          onClick={() => onPopOut({ body, title: titleField?.value })}
          className="p-1 rounded text-muted hover:bg-bg"
          aria-label="Open in a separate window"
          data-tooltip="Open in a separate window"
        >
          <ExternalLink size={14} />
        </button>
      )}
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
  );

  const main = (
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
  );

  const errorBar = error ? (
    <div className="px-3 pb-2 text-xs text-red-500 break-words">{error}</div>
  ) : null;

  const footerBar = footer ? (
    <div className="flex flex-wrap items-center justify-end gap-2 px-3 py-2 border-t border-border">
      {footer}
    </div>
  ) : null;

  if (embedded) {
    return (
      <div className="flex h-full w-full flex-col bg-surface text-text">
        {header}
        {main}
        {errorBar}
        {footerBar}
      </div>
    );
  }

  return createPortal(
    <div
      style={style}
      className="z-50 flex flex-col border border-border rounded-lg bg-surface shadow-2xl"
    >
      {header}
      {main}
      {errorBar}
      {footerBar}

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
