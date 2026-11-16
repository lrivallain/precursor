import { RefreshCw } from "lucide-react";

/** Separator drawn where recording resumed or transparently reconnected. */
export function RecordingBoundary({ kind }: { kind: "resume" | "reconnect" | undefined }) {
  if (kind === "resume") {
    return (
      <div className="my-3 flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
        <div className="h-px flex-1 bg-border" />
        Recording resumed
        <div className="h-px flex-1 bg-border" />
      </div>
    );
  }
  if (kind !== "reconnect") return null;
  return (
    <div className="my-3 flex items-center gap-2 text-[10px] uppercase tracking-wide text-amber-600 dark:text-amber-500">
      <div className="h-px flex-1 bg-amber-500/30" />
      <RefreshCw size={11} aria-hidden="true" />
      Reconnected
      <div className="h-px flex-1 bg-amber-500/30" />
    </div>
  );
}

/** Inline correction of one phrase: Enter saves, Shift+Enter breaks, Escape cancels. */
export function SegmentTextEditor({
  value,
  onChange,
  onCommit,
  onCancel,
}: {
  value: string;
  onChange: (value: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  return (
    <textarea
      autoFocus
      rows={1}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onCommit}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          onCommit();
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
      className="w-full resize-none rounded border border-border bg-surface px-1.5 py-0.5 text-sm text-text focus:border-accent focus:outline-none"
    />
  );
}
