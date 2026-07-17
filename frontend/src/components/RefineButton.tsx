import { Loader2, RotateCcw, Sparkles } from "lucide-react";

export interface RefineButtonProps {
  busy: boolean;
  canRevert: boolean;
  disabled?: boolean;
  error?: string | null;
  onClick: () => void;
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
  className,
}: RefineButtonProps) {
  const label = error
    ? error
    : busy
      ? "Refining…"
      : canRevert
        ? "Revert to your text"
        : "Refine with AI";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      title={label}
      aria-label={label}
      className={`absolute bottom-1.5 right-1.5 z-10 inline-flex h-6 w-6 items-center justify-center rounded border border-border bg-surface/80 text-muted backdrop-blur-sm transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40 ${
        error ? "border-red-500/50 text-red-500" : ""
      } ${className ?? ""}`}
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
