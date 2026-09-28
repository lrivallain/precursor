import { Suspense, lazy, useEffect, useState } from "react";
import type { KeyboardEvent } from "react";
import {
  Columns2,
  FileText,
  GitCommitHorizontal,
  Loader2,
  RotateCcw,
  Rows2,
  Square,
  SquareCheck,
  SquareMinus,
  Upload,
  X,
  ChevronLeft,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  FileVersions,
  GitActionResult,
  GitCommitDetail,
  GitCommitFile,
  GitFileStatus,
} from "../lib/types";
import type { ConfirmOptions } from "./ConfirmDialog";

// Monaco's diff editor, in the same lazy chunk family as the file editor.
const DiffEditor = lazy(() =>
  import("./DiffEditor")
    .then((m) => ({ default: m.DiffEditor }))
    .catch(() => ({
      default: () => (
        <p className="p-6 text-sm text-muted">The diff viewer couldn&apos;t load. Reload the page.</p>
      ),
    })),
);

/** A file HEAD doesn't have: discarding it deletes it. */
export function isNewFile(file: GitFileStatus): boolean {
  return file.code === "??" || file.code[0] === "A";
}

/** What the diff pane shows: a change in the working copy, or a file in a commit. */
export type DiffTarget =
  | { kind: "working"; file: GitFileStatus }
  | { kind: "commit"; commit: GitCommitDetail; file: GitCommitFile };

/** A file's change in a commit, marked like the Changes list marks them. */
export function commitFileMark(file: GitCommitFile): {
  letter: string;
  className: string;
  tip: string;
} {
  switch (file.status) {
    case "A":
      return { letter: "A", className: "text-emerald-500", tip: "Added" };
    case "D":
      return { letter: "D", className: "text-red-500", tip: "Deleted" };
    case "R":
      return { letter: "R", className: "text-blue-500", tip: `Renamed from ${file.orig_path ?? "?"}` };
    case "C":
      return { letter: "C", className: "text-blue-500", tip: `Copied from ${file.orig_path ?? "?"}` };
    default:
      return { letter: file.status || "M", className: "text-amber-500", tip: "Modified" };
  }
}

function status(file: GitFileStatus): { letter: string; className: string; tip: string } {
  if (file.conflicted) return { letter: "!", className: "text-red-500", tip: "Conflict" };
  if (file.code === "??") return { letter: "U", className: "text-emerald-500", tip: "New (untracked)" };
  if (file.code[0] === "A") return { letter: "A", className: "text-emerald-500", tip: "Added" };
  if (file.code[0] === "R")
    return { letter: "R", className: "text-blue-500", tip: `Renamed from ${file.orig_path ?? "?"}` };
  if (file.code.includes("D")) return { letter: "D", className: "text-red-500", tip: "Deleted" };
  return { letter: "M", className: "text-amber-500", tip: "Modified" };
}

/** What Discard will do to this file, said plainly before it happens. */
export function discardPrompt(file: GitFileStatus): ConfirmOptions {
  if (file.orig_path) {
    return {
      message:
        `"${file.orig_path}" was renamed to "${file.path}". Discarding restores ` +
        `"${file.orig_path}" as it was in the last commit and removes "${file.path}".`,
      confirmLabel: "Discard rename",
      variant: "warning",
    };
  }
  if (isNewFile(file)) {
    return {
      message: `"${file.path}" is new — discarding deletes it.`,
      confirmLabel: "Delete file",
      variant: "danger",
    };
  }
  return {
    message: `Discard local changes to "${file.path}"?`,
    confirmLabel: "Discard changes",
    variant: "warning",
  };
}

function splitPath(path: string): { name: string; dir: string } {
  const at = path.lastIndexOf("/");
  return at === -1 ? { name: path, dir: "" } : { name: path.slice(at + 1), dir: path.slice(0, at) };
}

// --------------------------------------------------------------------------
// Changes: what to commit, and the commit itself
// --------------------------------------------------------------------------

export function ChangesPanel({
  files,
  activeDiff,
  canPush,
  onOpenDiff,
  onOpenFile,
  onDiscard,
  onCommit,
  onCommitPush,
}: {
  files: GitFileStatus[];
  /** The repository path whose diff is showing. */
  activeDiff: string | null;
  /** False on a detached HEAD: there is no branch to push. */
  canPush: boolean;
  onOpenDiff: (file: GitFileStatus) => void;
  /** Tree path to open in the editor. */
  onOpenFile: (browsePath: string) => void;
  onDiscard: (file: GitFileStatus) => Promise<void>;
  onCommit: (message: string, paths: string[]) => Promise<GitActionResult>;
  onCommitPush: (message: string, paths: string[]) => Promise<GitActionResult>;
}) {
  // Unticked files; anything that appears later starts ticked, like VS Code.
  const [excluded, setExcluded] = useState<Set<string>>(() => new Set());
  const [message, setMessage] = useState("");
  const [running, setRunning] = useState<"commit" | "push" | null>(null);

  const selected = files.filter((f) => !excluded.has(f.path)).map((f) => f.path);
  const all = files.length > 0 && selected.length === files.length;
  const canCommit = running === null && selected.length > 0 && message.trim().length > 0;

  function toggle(path: string): void {
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function commit(andPush: boolean): Promise<void> {
    if (!canCommit) return;
    setRunning(andPush ? "push" : "commit");
    try {
      const run = andPush ? onCommitPush : onCommit;
      const res = await run(message.trim(), selected);
      if (res.ok || res.status?.ahead) setMessage("");
    } finally {
      setRunning(null);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>): void {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void commit(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col" aria-label="Changes">
      <div className="space-y-2 border-b border-border p-2">
        <textarea
          className="w-full resize-none rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
          rows={2}
          placeholder="Commit message"
          aria-label="Commit message"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="flex gap-1.5">
          <button
            className={`inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50 ${
              canPush ? "shrink-0" : "flex-1"
            }`}
            disabled={!canCommit}
            data-tooltip={"Commit the ticked files here only (⌘/Ctrl+Enter).\nPush sends them later."}
            onClick={() => void commit(false)}
          >
            {running === "commit" ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <GitCommitHorizontal size={13} />
            )}
            Commit
          </button>
          {canPush && (
            <button
              className="inline-flex min-w-0 flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded bg-accent px-2 py-1 text-xs text-white disabled:opacity-50"
              disabled={!canCommit}
              onClick={() => void commit(true)}
            >
              {running === "push" ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Upload size={13} />
              )}
              Commit &amp; Push
            </button>
          )}
        </div>
      </div>

      {files.length === 0 ? (
        <p className="px-3 py-3 text-xs text-muted">No changes to commit.</p>
      ) : (
        <>
          <button
            className="flex h-8 items-center gap-2 border-b border-border px-3 text-xs text-muted hover:bg-surface/60"
            onClick={() =>
              setExcluded(all ? new Set(files.map((f) => f.path)) : new Set())
            }
          >
            {all ? (
              <SquareCheck size={14} className="text-accent" />
            ) : selected.length === 0 ? (
              <Square size={14} />
            ) : (
              <SquareMinus size={14} className="text-accent" />
            )}
            {selected.length} of {files.length} to commit
          </button>
          <ul className="min-h-0 flex-1 overflow-auto py-1">
            {files.map((file) => {
              const { name, dir } = splitPath(file.path);
              const mark = status(file);
              const ticked = !excluded.has(file.path);
              return (
                <li
                  key={file.path}
                  className={`group flex items-center gap-1 pl-1.5 pr-1 ${
                    activeDiff === file.path ? "bg-surface" : "hover:bg-surface/60"
                  }`}
                >
                  <button
                    className="shrink-0 p-1 text-muted hover:text-text"
                    aria-label={ticked ? `Leave ${file.path} out` : `Include ${file.path}`}
                    aria-pressed={ticked}
                    onClick={() => toggle(file.path)}
                  >
                    {ticked ? (
                      <SquareCheck size={14} className="text-accent" />
                    ) : (
                      <Square size={14} />
                    )}
                  </button>
                  <button
                    className="flex min-w-0 flex-1 items-center gap-1.5 py-1 text-left text-sm"
                    aria-label={`${file.path}, ${mark.tip.toLowerCase()}`}
                    data-tooltip={`${file.path}\n${mark.tip} — click to see the changes`}
                    onClick={() => onOpenDiff(file)}
                  >
                    <span className={`w-3 shrink-0 text-center font-mono text-[11px] ${mark.className}`}>
                      {mark.letter}
                    </span>
                    <span className="truncate">{name}</span>
                    {dir && <span className="truncate text-xs text-muted">{dir}</span>}
                  </button>
                  {file.browse_path != null && !file.code.includes("D") && (
                    <button
                      className="shrink-0 rounded p-1 text-muted opacity-0 hover:text-text group-hover:opacity-100 focus:opacity-100"
                      aria-label={`Open ${file.path}`}
                      data-tooltip="Open file"
                      onClick={() => onOpenFile(file.browse_path as string)}
                    >
                      <FileText size={13} />
                    </button>
                  )}
                  <button
                    className="shrink-0 rounded p-1 text-muted opacity-0 hover:text-red-500 group-hover:opacity-100 focus:opacity-100"
                    aria-label={`Discard ${file.path}`}
                    data-tooltip={isNewFile(file) ? "Delete this new file" : "Discard changes"}
                    onClick={() => void onDiscard(file)}
                  >
                    <RotateCcw size={13} />
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Diff: HEAD against the working copy, in Monaco
// --------------------------------------------------------------------------

const INLINE_KEY = "precursor:workspace:diffInline";

export function GitDiffPane({
  workspaceId,
  target,
  narrow,
  version,
  onOpenFile,
  onClose,
}: {
  workspaceId: number;
  target: DiffTarget;
  narrow: boolean;
  /** Bumps when the working copy may have changed (save, pull, discard). */
  version: unknown;
  onOpenFile?: () => void;
  onClose: () => void;
}) {
  const [versions, setVersions] = useState<FileVersions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [inlinePref, setInlinePref] = useState(() => localStorage.getItem(INLINE_KEY) === "1");
  // A phone has no room for two columns.
  const inline = narrow || inlinePref;

  const file = target.file;
  const commit = target.kind === "commit" ? target.commit : null;
  // A commit is compared with the parent the server resolved (none for a
  // root commit, whose files are all added).
  const base = commit ? commit.parent : undefined;
  const head = commit?.sha;
  const key = `${commit?.sha ?? "working"}:${file.path}`;

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setVersions(null);
    api.workspaces
      .gitFileVersions(workspaceId, file.path, { originalPath: file.orig_path, base, head })
      .then((v) => {
        if (!cancelled) setVersions(v);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
    // A commit's files never change; the working copy's follow `version`.
  }, [workspaceId, key, file.orig_path, base, head, commit ? null : version]);

  function setInline(value: boolean): void {
    setInlinePref(value);
    localStorage.setItem(INLINE_KEY, value ? "1" : "0");
  }

  const mark =
    target.kind === "working" ? status(target.file) : commitFileMark(target.file);
  const loaded = versions?.path === file.path ? versions : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-4">
        {narrow && (
          <button
            type="button"
            className="-ml-2 shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label={commit ? "Back to history" : "Back to changes"}
            onClick={onClose}
          >
            <ChevronLeft size={16} />
          </button>
        )}
        <span className={`shrink-0 font-mono text-xs ${mark.className}`} data-tooltip={mark.tip}>
          {mark.letter}
        </span>
        <span className="min-w-0 flex-1 truncate text-sm">
          {file.orig_path && <span className="text-muted">{file.orig_path} → </span>}
          {file.path}
          {commit ? (
            <span className="text-muted" data-tooltip={commit.subject}>
              {" · in "}
              <span className="font-mono">{commit.short_sha}</span>
              {commit.parents.length > 1 && " · compared with its first parent"}
              {commit.parent === null && " · the first commit"}
            </span>
          ) : (
            <span className="text-muted"> · since the last commit</span>
          )}
        </span>
        {!narrow && (
          <div className="flex shrink-0 overflow-hidden rounded border border-border text-xs">
            <button
              className={`inline-flex items-center gap-1 px-2 py-1 ${
                inline ? "hover:bg-surface/60" : "bg-surface"
              }`}
              aria-pressed={!inline}
              onClick={() => setInline(false)}
            >
              <Columns2 size={13} /> Side by side
            </button>
            <button
              className={`inline-flex items-center gap-1 px-2 py-1 ${
                inline ? "bg-surface" : "hover:bg-surface/60"
              }`}
              aria-pressed={inline}
              onClick={() => setInline(true)}
            >
              <Rows2 size={13} /> Inline
            </button>
          </div>
        )}
        {onOpenFile && (
          <button
            className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Open file"
            data-tooltip="Open the file to edit it"
            onClick={onOpenFile}
          >
            <FileText size={15} />
          </button>
        )}
        {!narrow && (
          <button
            className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
            aria-label="Close diff"
            data-tooltip="Close"
            onClick={onClose}
          >
            <X size={15} />
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1">
        {error ? (
          <p className="p-6 text-sm text-red-500">{error}</p>
        ) : !loaded ? (
          <div className="flex h-full items-center justify-center text-muted">
            <Loader2 className="animate-spin" size={18} />
          </div>
        ) : loaded.binary ? (
          <p className="p-6 text-sm text-muted">Binary file — no text diff to show.</p>
        ) : loaded.too_large ? (
          <p className="p-6 text-sm text-muted">
            This file is over 2 MB — too large to compare here. Open it in VS Code instead.
          </p>
        ) : (
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-muted">
                <Loader2 className="animate-spin" size={18} />
              </div>
            }
          >
            <DiffEditor
              path={file.path}
              original={loaded.original ?? ""}
              modified={loaded.modified ?? ""}
              inline={inline}
            />
          </Suspense>
        )}
      </div>
    </div>
  );
}
