import { useResizableHeight } from "../lib/useResizableHeight";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Persists the chosen height; one key per distinct panel usage. */
  storageKey: string;
  defaultHeight?: number;
  minHeight?: number;
  maxHeight?: number;
  placeholder?: string;
  disabled?: boolean;
  /** Extra classes merged onto the textarea (theme tokens, font, etc.). */
  className?: string;
  "aria-label"?: string;
}

/**
 * A textarea with a draggable bottom edge whose height is remembered across
 * sessions (localStorage via ``useResizableHeight``). Shared by the build-in
 * command panels (notes / GitHub drafts) so they get a consistently larger,
 * user-sizable editing area instead of a fixed ``rows`` box.
 */
export function ResizableTextarea({
  value,
  onChange,
  storageKey,
  defaultHeight = 220,
  minHeight = 120,
  maxHeight = 640,
  placeholder,
  disabled = false,
  className = "",
  "aria-label": ariaLabel,
}: Props) {
  const { height, onMouseDown } = useResizableHeight({
    storageKey,
    defaultHeight,
    min: minHeight,
    max: maxHeight,
    side: "bottom",
  });

  return (
    <div className="relative">
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        aria-label={ariaLabel}
        style={{ height }}
        className={`w-full resize-none bg-bg border border-border rounded p-2 text-sm outline-none focus:border-accent disabled:opacity-60 ${className}`}
      />
      <div
        role="separator"
        aria-orientation="horizontal"
        onMouseDown={onMouseDown}
        title="Drag to resize"
        className="absolute -bottom-1 left-0 right-0 h-2 cursor-row-resize select-none group z-10"
      >
        <div className="h-px w-12 mx-auto mt-1 bg-border group-hover:bg-accent/60 transition-colors" />
      </div>
    </div>
  );
}
