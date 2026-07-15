import { Archive, CheckSquare, ListChecks, Square, X } from "lucide-react";

/** Small icon button that switches a list into multi-select mode. */
export function SelectToggleButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="shrink-0 rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-text"
      aria-label="Select multiple"
      data-tooltip="Select multiple"
    >
      <ListChecks size={15} />
    </button>
  );
}

interface SelectionToolbarProps {
  count: number;
  /** Whether every visible item is currently selected. */
  allSelected: boolean;
  onToggleAll: () => void;
  onArchive: () => void;
  onCancel: () => void;
  busy?: boolean;
}

/**
 * The action bar shown while a sidebar list is in multi-select mode: toggle-all,
 * a live count, and the bulk Archive / Cancel actions.
 */
export function SelectionToolbar({
  count,
  allSelected,
  onToggleAll,
  onArchive,
  onCancel,
  busy = false,
}: SelectionToolbarProps) {
  return (
    <div className="flex items-center gap-2 border-b border-border bg-surface px-3 py-1.5">
      <button
        type="button"
        onClick={onToggleAll}
        className="flex items-center gap-1.5 text-sm text-muted hover:text-text"
        aria-label={allSelected ? "Clear selection" : "Select all"}
      >
        {allSelected ? (
          <CheckSquare size={15} className="text-accent" />
        ) : (
          <Square size={15} />
        )}
        <span>{count} selected</span>
      </button>
      <div className="ml-auto flex items-center gap-1">
        <button
          type="button"
          onClick={onArchive}
          disabled={count === 0 || busy}
          className="flex items-center gap-1 rounded bg-accent px-2 py-1 text-xs text-white disabled:opacity-50"
        >
          <Archive size={13} />
          Archive
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="flex items-center gap-1 rounded border border-border px-2 py-1 text-xs text-muted hover:text-text disabled:opacity-50"
          aria-label="Cancel selection"
        >
          <X size={13} />
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Per-item checkbox indicator shown at the left of a list row in select mode. */
export function SelectionCheckbox({ checked }: { checked: boolean }) {
  return checked ? (
    <CheckSquare size={15} className="shrink-0 text-accent" />
  ) : (
    <Square size={15} className="shrink-0 text-muted" />
  );
}
