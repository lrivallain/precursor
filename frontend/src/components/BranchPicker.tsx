import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Check, ChevronDown, CloudDownload, GitBranch, GitBranchPlus, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import type { GitBranches } from "../lib/types";

// What git would refuse as a branch name, checked before asking the server
// (which checks again): spaces, `..`, `~^:?*[\`, a leading `-` or `/`, …
function nameProblem(name: string): string | null {
  if (!name) return null;
  if (/[\s~^:?*[\\]/.test(name) || name.includes("..") || name.includes("@{"))
    return "Spaces and ~ ^ : ? * [ \\ .. aren't allowed";
  if (/^[-/.]|[/.]$|\/\/|\.lock$|\/\./.test(name) || name === "HEAD")
    return "Not a valid branch name";
  return null;
}

/**
 * The git bar's branch name, as a picker: switch to a local branch or one only
 * the remote has, or create one from the current commit.
 */
export function BranchPicker({
  workspaceId,
  current,
  detached,
  onSwitch,
  onCreate,
}: {
  workspaceId: number;
  current: string;
  detached: boolean;
  /** Resolves false when nothing changed (refused, or cancelled). */
  onSwitch: (name: string) => Promise<boolean>;
  onCreate: (name: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [list, setList] = useState<GitBranches | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setList(null);
    setLoadError(null);
    api.workspaces
      .gitBranches(workspaceId)
      .then((b) => {
        if (!cancelled) setList(b);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, workspaceId]);

  const q = query.trim();
  const local = useMemo(
    () => (list?.local ?? []).filter((b) => b.name.toLowerCase().includes(q.toLowerCase())),
    [list, q],
  );
  const remoteOnly = useMemo(() => {
    const mine = new Set((list?.local ?? []).map((b) => b.name));
    return (list?.remote ?? []).filter(
      (name) => !mine.has(name) && name.toLowerCase().includes(q.toLowerCase()),
    );
  }, [list, q]);
  const exists =
    !!list && (list.local.some((b) => b.name === q) || list.remote.includes(q));
  const problem = nameProblem(q);
  const canCreate = !!list && q.length > 0 && !exists && !problem;

  async function run(name: string, action: (name: string) => Promise<boolean>): Promise<void> {
    setBusy(name);
    try {
      if (await action(name)) {
        setOpen(false);
        setQuery("");
      }
    } finally {
      setBusy(null);
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "Escape") setOpen(false);
    if (e.key === "Enter") {
      e.preventDefault();
      const only = [...local.map((b) => b.name), ...remoteOnly];
      if (only.includes(q)) void run(q, onSwitch);
      else if (canCreate) void run(q, onCreate);
      else if (only.length === 1) void run(only[0], onSwitch);
    }
  }

  const row = (active: boolean) =>
    `flex w-full items-center gap-2 rounded px-2 py-1 text-left text-sm ${
      active ? "text-text" : "hover:bg-bg disabled:opacity-50"
    }`;

  return (
    <div ref={ref} className="relative min-w-0">
      <button
        type="button"
        className="inline-flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-muted hover:bg-surface hover:text-text"
        aria-label={`Branch: ${detached ? "detached HEAD" : current}. Switch or create a branch`}
        aria-expanded={open}
        aria-haspopup="dialog"
        data-tooltip={
          detached
            ? "No branch is checked out, so Pull and Push are off.\nPick one to switch to it, or create one here."
            : "Switch or create a branch"
        }
        onClick={() => {
          setQuery("");
          setOpen(!open);
        }}
      >
        <GitBranch size={14} className="shrink-0" />
        {detached ? (
          <span className="truncate text-amber-500">detached HEAD</span>
        ) : (
          <span className="truncate">{current}</span>
        )}
        <ChevronDown size={12} className="shrink-0" />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Branches"
          className="absolute left-0 z-30 mt-1 w-72 max-w-[calc(100vw-2rem)] rounded-md border border-border bg-surface shadow-lg"
        >
          <div className="border-b border-border p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Find or create a branch…"
              aria-label="Find or create a branch"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-sm outline-none focus:border-accent"
            />
            {q && problem && <p className="mt-1 px-1 text-xs text-amber-500">{problem}</p>}
          </div>
          <div className="max-h-72 overflow-y-auto p-1">
            {loadError ? (
              <p className="px-2 py-1.5 text-xs text-red-500">{loadError}</p>
            ) : !list ? (
              <div className="flex justify-center py-3 text-muted">
                <Loader2 size={16} className="animate-spin" />
              </div>
            ) : (
              <>
                {canCreate && (
                  <button
                    type="button"
                    className={row(false)}
                    disabled={busy !== null}
                    onClick={() => void run(q, onCreate)}
                  >
                    {busy === q ? (
                      <Loader2 size={14} className="shrink-0 animate-spin" />
                    ) : (
                      <GitBranchPlus size={14} className="shrink-0 text-accent" />
                    )}
                    <span className="min-w-0 truncate">
                      Create <span className="font-medium">{q}</span>
                      <span className="text-muted"> from {detached ? "this commit" : current}</span>
                    </span>
                  </button>
                )}
                {local.length > 0 && (
                  <p className="px-2 pb-0.5 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                    Branches
                  </p>
                )}
                {local.map((b) => {
                  const here = b.name === list.current;
                  return (
                    <button
                      key={b.name}
                      type="button"
                      className={row(here)}
                      aria-current={here ? "true" : undefined}
                      disabled={here || busy !== null}
                      onClick={() => void run(b.name, onSwitch)}
                    >
                      {busy === b.name ? (
                        <Loader2 size={14} className="shrink-0 animate-spin" />
                      ) : here ? (
                        <Check size={14} className="shrink-0 text-accent" />
                      ) : (
                        <span className="w-[14px] shrink-0" />
                      )}
                      <span className="min-w-0 flex-1 truncate">{b.name}</span>
                      {!b.upstream && (
                        <span
                          className="shrink-0 rounded border border-border px-1 text-[10px] text-muted"
                          data-tooltip="Not on the remote yet"
                        >
                          not published
                        </span>
                      )}
                    </button>
                  );
                })}
                {remoteOnly.length > 0 && (
                  <p className="px-2 pb-0.5 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                    On the remote
                  </p>
                )}
                {remoteOnly.map((name) => (
                  <button
                    key={name}
                    type="button"
                    className={row(false)}
                    disabled={busy !== null}
                    data-tooltip="Only on the remote: switching creates a local branch that tracks it"
                    onClick={() => void run(name, onSwitch)}
                  >
                    {busy === name ? (
                      <Loader2 size={14} className="shrink-0 animate-spin" />
                    ) : (
                      <CloudDownload size={14} className="shrink-0 text-muted" />
                    )}
                    <span className="min-w-0 flex-1 truncate">{name}</span>
                  </button>
                ))}
                {local.length === 0 && remoteOnly.length === 0 && !canCreate && (
                  <p className="px-2 py-1.5 text-xs text-muted">No branch matches.</p>
                )}
                {list.remote_error && (
                  <p className="border-t border-border px-2 pt-1.5 text-xs text-muted">
                    Couldn&apos;t reach the remote — only local branches are listed.
                  </p>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
