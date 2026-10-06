import { useEffect, useState } from "react";
import { ArrowDown, History, X } from "lucide-react";
import { rewindOffered, type RewindController, type RewindFiles } from "../lib/useRewind";
import type { TurnTimeline } from "../lib/useTurnTimeline";

interface RewindBarProps {
  timeline: TurnTimeline;
  rewind: RewindController;
  streaming: boolean;
  scrollRef: React.RefObject<HTMLDivElement | null>;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * The strip above the composer that drives a rewind: a "Reading turn N" bar
 * while the reader is scrolled back, the confirmation of a previewed cut, and
 * the undo countdown once it is confirmed. An agent rewind also offers to restore
 * the files the dropped turns changed, and reports what it restored.
 */
export function RewindBar({ timeline, rewind, streaming, scrollRef }: RewindBarProps) {
  const { preview, pending } = rewind;
  const { turns, view } = timeline;

  useEffect(() => {
    if (!preview) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") rewind.cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, rewind]);

  const announce = pending
    ? `${plural(pending.droppedTurns, "turn")} removed. Undo is available for a few seconds.`
    : preview
      ? `Rewind preview: the dimmed turns will be deleted${
          rewind.irreversible ? " for good" : ""
        }. Press Escape to cancel.`
      : "";

  let body: React.ReactNode = null;
  if (pending) {
    body = <PendingRow pending={pending} onUndo={rewind.undo} />;
  } else if (preview) {
    const n = preview.index + 1;
    body = (
      <div className="rounded-lg border border-amber-500/60 bg-amber-500/10 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-3">
          <span className="min-w-0 flex-1">
            {preview.mode === "rewind" ? (
              <>
                Rewind to <strong>turn {n}</strong>: {plural(preview.droppedTurns, "later turn")} will
                be deleted.
              </>
            ) : (
              <>
                Edit <strong>turn {n}</strong>&rsquo;s prompt: it and{" "}
                {plural(preview.droppedTurns - 1, "later turn")} will be deleted.
              </>
            )}
            {rewind.irreversible && (
              <strong className="text-amber-800 dark:text-amber-200"> This can&rsquo;t be undone.</strong>
            )}
          </span>
          <button
            type="button"
            onClick={rewind.cancel}
            className="rounded-md border border-border px-2 py-1 hover:bg-surface"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={rewind.confirm}
            disabled={streaming || rewind.busy || rewind.files?.loading}
            className="rounded-md border border-amber-500 bg-amber-500 px-2 py-1 font-medium text-black hover:bg-amber-400 disabled:opacity-40"
          >
            {preview.mode === "rewind" ? "Rewind" : "Edit & resend"}
          </button>
        </div>
        {rewind.files && <FilesRow files={rewind.files} disabled={Boolean(rewind.busy)} />}
      </div>
    );
  } else if (
    !streaming &&
    !view.atLatest &&
    view.current >= 0 &&
    view.current < turns.length - 1
  ) {
    const later = turns.length - 1 - view.current;
    const reading = view.current;
    body = (
      <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs">
        <span className="min-w-0 flex-1 truncate text-muted">
          Reading <strong className="text-text">turn {reading + 1}</strong> of {turns.length} ·{" "}
          {plural(later, "later turn")}
        </span>
        {rewindOffered(rewind, reading, turns.length, "rewind") && (
          <button
            type="button"
            disabled={rewind.busy}
            onClick={() => {
              rewind.start(turns, reading, "rewind");
              void timeline.jumpTo(reading);
            }}
            data-tooltip="Keep this turn and drop the later ones"
            className="inline-flex items-center gap-1 rounded-md border border-amber-500/60 bg-amber-500/15 px-2 py-1 text-amber-800 hover:bg-amber-500/25 disabled:opacity-40 dark:text-amber-200"
          >
            <History size={12} aria-hidden /> Rewind to here
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            const box = scrollRef.current;
            if (box) box.scrollTop = box.scrollHeight;
          }}
          className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:bg-bg"
        >
          <ArrowDown size={12} aria-hidden /> Latest
        </button>
      </div>
    );
  }

  const notice = !preview && !pending ? rewind.notice : null;
  return (
    <>
      {/* Always mounted: screen readers skip a live region inserted with its text. */}
      <span className="sr-only" role="status">
        {announce || notice?.text || ""}
      </span>
      {notice && (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-1.5 text-xs">
          <span className="min-w-0 flex-1 truncate text-muted" data-tooltip={notice.detail}>
            {notice.text}
          </span>
          <button
            type="button"
            onClick={rewind.dismissNotice}
            className="shrink-0 rounded p-0.5 text-muted hover:text-text"
            aria-label="Dismiss"
            data-tooltip="Dismiss"
          >
            <X size={12} aria-hidden />
          </button>
        </div>
      )}
      {body}
    </>
  );
}

const CHANGE_LABEL: Record<string, string> = {
  created: "new",
  modified: "edited",
  deleted: "deleted",
};

const basename = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path;

/** The opt-in to restore the files a previewed agent rewind would put back. */
function FilesRow({ files, disabled }: { files: RewindFiles; disabled: boolean }) {
  if (files.loading) {
    return <p className="mt-1 text-[11px] text-muted">Checking the files these turns changed…</p>;
  }
  if (files.unavailable) {
    return <p className="mt-1 text-[11px] text-muted">{files.unavailable}</p>;
  }
  const added = files.files.reduce((n, f) => n + f.lines_added, 0);
  const removed = files.files.reduce((n, f) => n + f.lines_removed, 0);
  return (
    <div className="mt-1.5 space-y-1">
      <label className="flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          checked={files.restore}
          disabled={disabled}
          onChange={(e) => files.setRestore(e.target.checked)}
          className="accent-amber-500"
        />
        <span>
          Also restore {plural(files.files.length, "file")} these turns changed{" "}
          <span className="text-muted">
            (<span className="text-emerald-700 dark:text-emerald-400">+{added}</span>{" "}
            <span className="text-red-700 dark:text-red-400">−{removed}</span>)
          </span>
        </span>
      </label>
      <details className="pl-5 text-[11px] text-muted">
        <summary className="cursor-pointer select-none hover:text-text">Show files</summary>
        <ul className="mt-1 max-h-32 space-y-0.5 overflow-y-auto">
          {files.files.map((f) => (
            <li key={f.path} className="flex items-center gap-2" data-tooltip={f.path}>
              <span className="min-w-0 flex-1 truncate font-mono text-text">{basename(f.path)}</span>
              <span>{CHANGE_LABEL[f.change_type] ?? f.change_type}</span>
              <span className="tabular-nums">
                +{f.lines_added} −{f.lines_removed}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-1">Files you changed since the agent did are left as they are.</p>
      </details>
    </div>
  );
}

function PendingRow({
  pending,
  onUndo,
}: {
  pending: NonNullable<RewindController["pending"]>;
  onUndo: () => void;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(handle);
  }, []);
  const seconds = Math.max(0, Math.ceil((pending.expiresAt - now) / 1000));
  return (
    <div className="flex items-center justify-between gap-3 rounded border border-border bg-surface px-3 py-1.5 text-xs">
      <span className="truncate text-muted">
        {pending.mode === "edit" ? "Prompt back in the composer · " : "Rewound · "}
        {plural(pending.droppedTurns, "turn")} removed · undo in {seconds}s
      </span>
      <button
        type="button"
        onClick={onUndo}
        className="shrink-0 text-accent hover:underline"
        aria-label={`Undo rewind: restore ${plural(pending.droppedTurns, "turn")}`}
      >
        Undo
      </button>
    </div>
  );
}
