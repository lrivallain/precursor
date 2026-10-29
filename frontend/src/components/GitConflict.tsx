import { Suspense, lazy, useEffect, useState } from "react";
import { ChevronLeft, FileText, Loader2, X } from "lucide-react";
import { api } from "../lib/api";
import type { GitConflict, GitFileStatus } from "../lib/types";

const DiffEditor = lazy(() =>
  import("./DiffEditor")
    .then((m) => ({ default: m.DiffEditor }))
    .catch(() => ({
      default: () => (
        <p className="p-6 text-sm text-muted">The diff viewer couldn&apos;t load. Reload the page.</p>
      ),
    })),
);

const WHAT_HAPPENED: Record<GitConflict["kind"], string> = {
  both_modified: "Both branches changed this file.",
  both_added: "Both branches added a file with this name, with different contents.",
  deleted_by_us: "Your branch deleted this file; the incoming branch changed it.",
  deleted_by_them: "Your branch changed this file; the incoming branch deleted it.",
  added_by_us: "Only your branch has this file, and the incoming changes can't be applied to it.",
  added_by_them: "Only the incoming branch has this file, and your changes can't be applied to it.",
  both_deleted: "Both branches deleted this file, in different ways.",
};

/**
 * One conflicted file during a merge: what happened, and how to resolve it.
 * A text conflict is resolved in the editor (Accept lenses above each block),
 * then marked resolved; a binary or delete/modify one by keeping one side.
 */
export function ConflictPane({
  workspaceId,
  file,
  narrow,
  version,
  onResolveInEditor,
  onMarkResolved,
  onKeep,
  onClose,
}: {
  workspaceId: number;
  file: GitFileStatus;
  narrow: boolean;
  /** Bumps when the working copy may have changed. */
  version: unknown;
  /** Open the file (with its conflict markers) in the editor; absent if it isn't on disk. */
  onResolveInEditor?: () => void;
  onMarkResolved: () => Promise<void>;
  onKeep: (side: "ours" | "theirs", conflict: GitConflict) => Promise<void>;
  onClose: () => void;
}) {
  const [conflict, setConflict] = useState<GitConflict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    api.workspaces
      .gitConflict(workspaceId, file.path)
      .then((c) => {
        if (!cancelled) setConflict(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, file.path, version]);

  async function run(label: string, action: () => Promise<void>): Promise<void> {
    setBusy(label);
    try {
      await action();
    } finally {
      setBusy(null);
    }
  }

  const loaded = conflict?.path === file.path ? conflict : null;
  const textual =
    !!loaded && !loaded.binary && !loaded.too_large && loaded.has_ours && loaded.has_theirs;
  const keepLabel = (side: "ours" | "theirs"): string => {
    const present = side === "ours" ? loaded?.has_ours : loaded?.has_theirs;
    const who = side === "ours" ? "yours" : "theirs";
    return present ? `Keep ${who}` : `Keep ${who} (delete the file)`;
  };
  const button =
    "inline-flex items-center gap-1.5 rounded border border-border px-2.5 py-1 text-xs hover:bg-surface disabled:opacity-50";

  return (
    <div className="flex h-full min-h-0 flex-col" aria-label="Conflict">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-4">
        {narrow && (
          <button
            type="button"
            className="-ml-2 shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Back to changes"
            onClick={onClose}
          >
            <ChevronLeft size={16} />
          </button>
        )}
        <span className="shrink-0 font-mono text-xs text-red-500" data-tooltip="Conflict">
          !
        </span>
        <span className="min-w-0 flex-1 truncate text-sm">
          {file.path}
          <span className="text-muted"> · merge conflict</span>
        </span>
        {!narrow && (
          <button
            className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Close conflict"
            data-tooltip="Close"
            onClick={onClose}
          >
            <X size={15} />
          </button>
        )}
      </div>
      {error ? (
        <p className="p-6 text-sm text-red-500">{error}</p>
      ) : !loaded ? (
        <div className="flex h-full items-center justify-center text-muted">
          <Loader2 className="animate-spin" size={18} />
        </div>
      ) : (
        <>
          <div className="space-y-2 border-b border-border px-4 py-3 text-sm">
            <p>{WHAT_HAPPENED[loaded.kind]}</p>
            {textual ? (
              <p className="text-muted">
                Some changes overlap. Resolve them in the editor: above each conflict, choose{" "}
                <b>Accept current</b>{" "}
                (yours), <b>Accept incoming</b> (theirs) or <b>Accept both</b>, or edit it by
                hand. Save, then <b>Mark resolved</b>. Or keep one version of the whole file.
              </p>
            ) : (
              <p className="text-muted">
                {loaded.binary
                  ? "It's a binary file, so it can't be merged line by line."
                  : loaded.too_large
                    ? "It's too large to compare here."
                    : "There's no pair of versions to merge line by line."}{" "}
                Keep one version; the other side&apos;s changes to this file are dropped.
              </p>
            )}
            <div className="flex flex-wrap gap-1.5">
              {textual && onResolveInEditor && (
                <button
                  className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1 text-xs text-white"
                  onClick={onResolveInEditor}
                >
                  <FileText size={13} /> Resolve in the editor
                </button>
              )}
              {textual && (
                <button
                  className={button}
                  disabled={busy !== null}
                  data-tooltip="Once no conflict marker is left in the saved file"
                  onClick={() => void run("mark", onMarkResolved)}
                >
                  {busy === "mark" && <Loader2 size={13} className="animate-spin" />}
                  Mark resolved
                </button>
              )}
              {(["ours", "theirs"] as const).map((side) => (
                <button
                  key={side}
                  className={button}
                  disabled={busy !== null}
                  onClick={() => void run(side, () => onKeep(side, loaded))}
                >
                  {busy === side && <Loader2 size={13} className="animate-spin" />}
                  {keepLabel(side)}
                </button>
              ))}
            </div>
          </div>
          {textual && (
            <div className="flex min-h-0 flex-1 flex-col">
              <p className="flex justify-between px-4 py-1 text-xs text-muted">
                <span>Yours (current)</span>
                <span>Theirs (incoming)</span>
              </p>
              <div className="min-h-0 flex-1">
                <Suspense
                  fallback={
                    <div className="flex h-full items-center justify-center text-muted">
                      <Loader2 className="animate-spin" size={18} />
                    </div>
                  }
                >
                  <DiffEditor
                    path={file.path}
                    original={loaded.ours ?? ""}
                    modified={loaded.theirs ?? ""}
                    inline={narrow}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
