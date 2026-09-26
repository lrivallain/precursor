import { useState } from "react";
import { FileCode2, FileWarning, FileX2, ShieldAlert } from "lucide-react";
import type { DefinitionSource } from "../lib/types";

/**
 * Files mode only: which definition file declares an agent or workflow, and
 * whether its permissions changed on disk and wait for review. Renders nothing
 * in database mode, where `source` is null.
 */
export function DefinitionBadge({
  source,
  onAccept,
}: {
  source: DefinitionSource | null | undefined;
  /** Accept the file's current permissions; the parent refreshes afterwards. */
  onAccept?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!source) return null;

  const review = source.review ?? [];
  if (source.state === "file" && review.length > 0) {
    const detail = `The file ${source.path} now grants:\n• ${review.join("\n• ")}\n\nIt can't run until you accept.`;
    return (
      <span className="inline-flex items-center gap-1.5">
        <span
          className="inline-flex items-center gap-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-300"
          data-tooltip={detail}
        >
          <ShieldAlert size={10} aria-hidden />
          Permissions changed
        </span>
        {onAccept && (
          <button
            type="button"
            disabled={busy}
            data-tooltip={error ?? detail}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await onAccept();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
            className="rounded border border-amber-500/40 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 transition hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300"
          >
            {busy ? "Accepting…" : "Review & accept"}
          </button>
        )}
      </span>
    );
  }
  if (source.state === "file") {
    return (
      <span
        className="inline-flex max-w-[16rem] items-center gap-1 rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-600 dark:text-sky-400"
        data-tooltip={`Declared by ${source.path}.\nSaving here writes that file.`}
      >
        <FileCode2 size={10} aria-hidden />
        <span className="truncate">{source.path}</span>
      </span>
    );
  }
  if (source.state === "invalid") {
    return (
      <span
        className="inline-flex max-w-[16rem] items-center gap-1 rounded bg-red-500/10 px-1.5 py-0.5 text-[10px] font-medium text-red-600 dark:text-red-400"
        data-tooltip={source.message ?? "Its definition file has errors"}
      >
        <FileWarning size={10} aria-hidden />
        <span className="truncate">{source.path}</span>
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400"
      data-tooltip={source.message ?? "No definition file yet"}
    >
      <FileX2 size={10} aria-hidden />
      No file
    </span>
  );
}
