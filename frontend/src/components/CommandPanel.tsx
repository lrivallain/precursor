import { type ReactNode, useState } from "react";
import { Eye, Loader2, Pencil, X } from "lucide-react";
import { Markdown } from "./Markdown";
import { ResizableTextarea } from "./ResizableTextarea";

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
  bodyPlaceholder?: string;
  /** Extra classes for the textarea (e.g. ``font-mono``). */
  bodyClassName?: string;
  /** Distinct key so each usage remembers its own height. */
  resizeStorageKey: string;
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
 * Provides a consistent header (icon, title, subtitle, Edit/Preview toggle,
 * close), a resizable Markdown editor with an optional title field, a live
 * Markdown preview, and a footer slot for each command's own action buttons.
 * Centralising this gives every command the same editing affordances (notably
 * the preview toggle, which `/notes` previously lacked).
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
  bodyPlaceholder = "Write in GitHub-Flavored Markdown…",
  bodyClassName = "",
  resizeStorageKey,
  previewEmptyHint = "Nothing to preview.",
  disabled = false,
  error,
  footer,
}: Props) {
  const [mode, setMode] = useState<Mode>("edit");
  const showPreviewTitle = titleField !== undefined && titleField.value.trim().length > 0;

  return (
    <div className="border border-border rounded-lg bg-surface shadow-sm">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <span className="text-muted shrink-0">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{title}</div>
          {subtitle && <div className="text-[11px] text-muted truncate">{subtitle}</div>}
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

      <div className="p-3 space-y-2">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" />
            {loadingLabel}
          </div>
        ) : mode === "edit" ? (
          <>
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
            <ResizableTextarea
              value={body}
              onChange={onBodyChange}
              storageKey={resizeStorageKey}
              className={bodyClassName}
              placeholder={bodyPlaceholder}
              disabled={disabled}
              aria-label={`${title} body`}
            />
          </>
        ) : (
          <div className="text-sm leading-relaxed bg-bg border border-border rounded p-3 min-h-[8rem]">
            {showPreviewTitle && (
              <h3 className="!mt-0 !mb-2 text-base font-semibold">{titleField.value.trim()}</h3>
            )}
            {body.trim() ? (
              <Markdown>{body}</Markdown>
            ) : (
              <span className="text-muted italic">{previewEmptyHint}</span>
            )}
          </div>
        )}
      </div>

      {error && <div className="px-3 pb-2 text-xs text-red-500 break-words">{error}</div>}

      {footer && (
        <div className="flex flex-wrap items-center justify-end gap-2 px-3 py-2 border-t border-border">
          {footer}
        </div>
      )}
    </div>
  );
}
