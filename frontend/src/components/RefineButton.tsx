import { Loader2, RotateCcw, Sparkles } from "lucide-react";

export interface RefineButtonProps {
  busy: boolean;
  canRevert: boolean;
  disabled?: boolean;
  error?: string | null;
  onClick: () => void;
  /** Shift left of the bottom-right resize grip on resizable textareas. */
  avoidResizeGrip?: boolean;
  /** Extra classes to tweak placement (defaults to bottom-right overlay). */
  className?: string;
}

/**
 * The little icon that sits in the bottom-right corner of a refinable textarea.
 * Shows a spinner while working, a revert arrow once a suggestion is applied,
 * and the sparkle otherwise.
 */
export function RefineButton({
  busy,
  canRevert,
  disabled,
  error,
  onClick,
  avoidResizeGrip,
  className,
}: RefineButtonProps) {
  const label = error
    ? error
    : busy
      ? "Refining…"
      : canRevert
        ? "Restore your original text"
        : "Refine with AI";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      data-tooltip={label}
      aria-label={label}
      className={`absolute right-2 z-10 inline-flex h-6 w-6 items-center justify-center rounded border border-border bg-surface/80 text-muted backdrop-blur-sm transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40 ${
        avoidResizeGrip ? "bottom-6" : "bottom-2"
      } ${error ? "border-red-500/50 text-red-500" : ""} ${className ?? ""}`}
    >
      {busy ? (
        <Loader2 size={13} className="animate-spin" />
      ) : canRevert ? (
        <RotateCcw size={13} />
      ) : (
        <Sparkles size={13} />
      )}
    </button>
  );
}
