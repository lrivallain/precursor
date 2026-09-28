import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, GitMerge, Loader2, X } from "lucide-react";
import { api } from "../lib/api";
import type { GitCommit, GitCommitDetail, GitCommitFile } from "../lib/types";
import { commitFileMark } from "./GitChanges";

const PAGE = 30;

function shortDate(iso: string): string {
  const date = new Date(iso);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  });
}

function splitPath(path: string): { name: string; dir: string } {
  const at = path.lastIndexOf("/");
  return at === -1 ? { name: path, dir: "" } : { name: path.slice(at + 1), dir: path.slice(0, at) };
}

/**
 * The file a commit changed that a file history is about: the same path, or
 * (before a rename) the only file it changed.
 */
function fileFor(detail: GitCommitDetail, path: string): GitCommitFile | null {
  return (
    detail.files.find((f) => f.path === path || f.orig_path === path) ??
    (detail.files.length === 1 ? detail.files[0] : null)
  );
}

/**
 * The checked-out branch's history, newest first, paged. A commit expands to
 * its message and files; a file opens its diff against the commit's parent.
 * Key it by workspace and `path`: another file's history is another list.
 */
export function HistoryPanel({
  workspaceId,
  head,
  path,
  onClearPath,
  activeKey,
  onOpenDiff,
}: {
  workspaceId: number;
  /** The checked-out commit: the list reloads when it moves (commit, pull). */
  head: string | null;
  /** Only this file's history (repository path). */
  path: string | null;
  onClearPath: () => void;
  /** `<sha>:<path>` of the diff showing. */
  activeKey: string | null;
  onOpenDiff: (commit: GitCommitDetail, file: GitCommitFile) => void;
}) {
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, GitCommitDetail>>({});
  // Only the latest request may fill the list: switching to a file's history
  // while the whole branch's is loading must not end on the wrong one.
  const latest = useRef(0);

  const load = useCallback(
    async (skip: number) => {
      const ticket = ++latest.current;
      setLoading(true);
      setError(null);
      try {
        const page = await api.workspaces.gitLog(workspaceId, { limit: PAGE, skip, path });
        if (ticket !== latest.current) return;
        setCommits((prev) => (skip === 0 ? page.commits : [...prev, ...page.commits]));
        setHasMore(page.has_more);
      } catch (e) {
        if (ticket === latest.current) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (ticket === latest.current) setLoading(false);
      }
    },
    [workspaceId, path],
  );

  useEffect(() => {
    setExpanded(null);
    void load(0);
  }, [load, head]);

  async function detailOf(sha: string): Promise<GitCommitDetail | null> {
    if (details[sha]) return details[sha];
    try {
      const detail = await api.workspaces.gitCommitDetail(workspaceId, sha);
      setDetails((prev) => ({ ...prev, [sha]: detail }));
      return detail;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return null;
    }
  }

  async function toggle(commit: GitCommit): Promise<void> {
    if (expanded === commit.sha) {
      setExpanded(null);
      return;
    }
    setExpanded(commit.sha);
    const detail = await detailOf(commit.sha);
    // In a file's history, the commit's point is that file: show it straight away.
    const file = detail && path ? fileFor(detail, path) : null;
    if (detail && file) onOpenDiff(detail, file);
  }

  return (
    <div className="flex h-full min-h-0 flex-col" aria-label="History">
      {path && (
        <div className="flex items-center gap-1.5 border-b border-border px-3 py-1.5 text-xs">
          <span className="min-w-0 flex-1 truncate text-muted">
            History of <span className="text-text">{path}</span>
          </span>
          <button
            className="shrink-0 rounded p-0.5 text-muted hover:bg-surface hover:text-text"
            aria-label="Show all history"
            data-tooltip="Show the whole branch's history"
            onClick={onClearPath}
          >
            <X size={13} />
          </button>
        </div>
      )}
      <ul className="min-h-0 flex-1 overflow-auto py-1">
        {commits.map((commit) => {
          const open = expanded === commit.sha;
          const detail = details[commit.sha];
          const merge = commit.parents.length > 1;
          return (
            <li key={commit.sha}>
              <button
                className={`flex w-full items-start gap-1.5 px-2 py-1.5 text-left ${
                  open ? "bg-surface" : "hover:bg-surface/60"
                }`}
                aria-expanded={open}
                onClick={() => void toggle(commit)}
              >
                {open ? (
                  <ChevronDown size={13} className="mt-0.5 shrink-0 text-muted" />
                ) : (
                  <ChevronRight size={13} className="mt-0.5 shrink-0 text-muted" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{commit.subject}</span>
                  <span className="flex items-center gap-1.5 text-xs text-muted">
                    <span className="font-mono">{commit.short_sha}</span>
                    <span className="truncate">{commit.author}</span>
                    <span
                      className="ml-auto shrink-0"
                      data-tooltip={new Date(commit.date).toLocaleString()}
                    >
                      {shortDate(commit.date)}
                    </span>
                  </span>
                </span>
                {merge && (
                  <GitMerge
                    size={13}
                    className="mt-0.5 shrink-0 text-muted"
                    aria-label="Merge commit"
                  />
                )}
              </button>
              {open && (
                <div className="border-b border-border pb-1.5 pl-6 pr-2">
                  {!detail ? (
                    <Loader2 size={14} className="my-1 animate-spin text-muted" />
                  ) : (
                    <>
                      {detail.body && (
                        <p className="mb-1 whitespace-pre-wrap text-xs text-muted">{detail.body}</p>
                      )}
                      {merge && (
                        <p className="mb-1 text-xs text-muted">
                          Files compared with its first parent.
                        </p>
                      )}
                      {detail.files.length === 0 && (
                        <p className="text-xs text-muted">No file changes.</p>
                      )}
                      <ul>
                        {detail.files.map((file) => {
                          const { name, dir } = splitPath(file.path);
                          const mark = commitFileMark(file);
                          const key = `${detail.sha}:${file.path}`;
                          return (
                            <li key={file.path}>
                              <button
                                className={`flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-sm ${
                                  activeKey === key ? "bg-surface" : "hover:bg-surface/60"
                                }`}
                                aria-label={`${file.path}, ${mark.tip.toLowerCase()}`}
                                data-tooltip={`${file.path}\n${mark.tip} — click to see the changes`}
                                onClick={() => onOpenDiff(detail, file)}
                              >
                                <span
                                  className={`w-3 shrink-0 text-center font-mono text-[11px] ${mark.className}`}
                                >
                                  {mark.letter}
                                </span>
                                <span className="truncate">{name}</span>
                                {dir && <span className="truncate text-xs text-muted">{dir}</span>}
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="px-3 py-2 text-xs text-red-500">{error}</p>}
      {!loading && !error && commits.length === 0 && (
        <p className="px-3 py-3 text-xs text-muted">
          {path ? "No commits touch this file yet." : "No commits yet."}
        </p>
      )}
      {(hasMore || loading) && (
        <div className="border-t border-border p-2">
          <button
            className="inline-flex w-full items-center justify-center gap-1.5 rounded border border-border px-2 py-1 text-xs hover:bg-surface disabled:opacity-50"
            disabled={loading}
            onClick={() => void load(commits.length)}
          >
            {loading && <Loader2 size={13} className="animate-spin" />}
            {loading ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
