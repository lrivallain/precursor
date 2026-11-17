import { ArrowDownToLine, ListChecks, Loader2, Trash2 } from "lucide-react";
import type { TranscriptSelection } from "./useTranscriptSelection";

const LINK = "rounded px-1.5 py-0.5 text-muted hover:bg-surface hover:text-text disabled:opacity-50";

/** Header button that enters / leaves the transcript's selection mode. */
export function TranscriptSelectToggle({
  selection,
  disabled,
}: {
  selection: TranscriptSelection;
  disabled: boolean;
}) {
  return (
    <button
      type="button"
      onClick={() => selection.setActive(!selection.active)}
      disabled={disabled && !selection.active}
      aria-pressed={selection.active}
      data-tooltip="Select phrases to discard"
      className={`ml-auto inline-flex items-center gap-1 rounded px-2 py-1 text-[12px] disabled:opacity-50 ${
        selection.active ? "bg-accent/10 text-accent" : "text-muted hover:bg-surface hover:text-text"
      }`}
    >
      <ListChecks size={14} aria-hidden="true" />
      {selection.active ? "Done" : "Select"}
    </button>
  );
}

/** Bulk actions shown above the transcript while selecting. */
export function TranscriptSelectionBar({ selection }: { selection: TranscriptSelection }) {
  const count = selection.selected.size;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border bg-accent/5 px-4 py-1.5 text-[12px]">
      <span className="font-medium text-text tabular-nums">{count} selected</span>
      <button type="button" className={LINK} onClick={selection.selectAll}>
        All
      </button>
      <button type="button" className={LINK} onClick={selection.invert}>
        Invert
      </button>
      <button type="button" className={LINK} onClick={selection.clear} disabled={count === 0}>
        Clear
      </button>
      <span className="hidden text-muted md:inline">
        Shift-click selects a range; <ArrowDownToLine size={11} className="inline" aria-hidden="true" />{" "}
        selects to the end.
      </span>
      {selection.error && <span className="text-red-500">{selection.error}</span>}
      <button
        type="button"
        onClick={() => void selection.discard()}
        disabled={count === 0 || selection.busy}
        className="ml-auto inline-flex items-center gap-1 rounded bg-red-600 px-2 py-1 text-white disabled:opacity-50"
      >
        {selection.busy ? (
          <Loader2 size={12} className="animate-spin" aria-hidden="true" />
        ) : (
          <Trash2 size={12} aria-hidden="true" />
        )}
        Discard{count ? ` ${count}` : ""}
      </button>
    </div>
  );
}

/** Per-phrase checkbox plus a "select from here to the end" shortcut. */
export function SelectGutter({
  selection,
  id,
}: {
  selection: TranscriptSelection;
  id: number;
}) {
  return (
    <span className="flex shrink-0 items-start gap-0.5 pt-0.5" onClick={(e) => e.stopPropagation()}>
      <input
        type="checkbox"
        checked={selection.selected.has(id)}
        onChange={() => undefined}
        onClick={(e) => selection.click(id, e.shiftKey)}
        aria-label="Select phrase"
        className="accent-accent"
      />
      <button
        type="button"
        onClick={() => selection.fromHere(id)}
        aria-label="Select from here to the end"
        data-tooltip="Select from here to the end"
        className="rounded text-muted opacity-0 hover:text-accent focus:opacity-100 group-hover:opacity-100"
      >
        <ArrowDownToLine size={12} aria-hidden="true" />
      </button>
    </span>
  );
}
