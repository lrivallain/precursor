import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Loader2, RotateCcw, Square, SquareCheck, Upload, X } from "lucide-react";
import { api } from "../lib/api";
import { useConfirm } from "./ConfirmDialog";
import type {
  FileDiff,
  GitActionResult,
  GitFileStatus,
  Workspace,
} from "../lib/types";

// --------------------------------------------------------------------------
// Changes review modal — preview diffs, choose which files to commit
// --------------------------------------------------------------------------

export function ChangesModal({
  area,
  files,
  onClose,
  onCommitPush,
  onDiscard,
}: {
  area: Workspace;
  files: GitFileStatus[];
  onClose: () => void;
  onCommitPush: (message: string, paths: string[]) => Promise<GitActionResult>;
  onDiscard: (path: string) => Promise<void>;
}) {
  const confirmAction = useConfirm();
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(files.map((f) => f.path)),
  );
  const [active, setActive] = useState<string | null>(files[0]?.path ?? null);
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [loadingDiff, setLoadingDiff] = useState(false);
  const [message, setMessage] = useState("Update workspace content");
  const [pushing, setPushing] = useState(false);

  const loadDiff = useCallback(
    async (path: string): Promise<void> => {
      setActive(path);
      setLoadingDiff(true);
      try {
        setDiff(await api.workspaces.gitDiff(area.id, path));
      } catch {
        setDiff({ path, diff: "(failed to load diff)", binary: false });
      } finally {
        setLoadingDiff(false);
      }
    },
    [area.id],
  );

  useEffect(() => {
    if (active) void loadDiff(active);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggle(path: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const allSelected = files.length > 0 && selected.size === files.length;
  function toggleAll(): void {
    setSelected(allSelected ? new Set() : new Set(files.map((f) => f.path)));
  }

  async function commit(): Promise<void> {
    if (selected.size === 0 || !message.trim()) return;
    setPushing(true);
    try {
      const res = await onCommitPush(message.trim(), [...selected]);
      if (res.ok) onClose();
    } finally {
      setPushing(false);
    }
  }

  async function discard(path: string): Promise<void> {
    if (
      !(await confirmAction({
        message: `Discard local changes to "${path}"?`,
        confirmLabel: "Discard changes",
        variant: "warning",
      }))
    )
      return;
    await onDiscard(path);
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(path);
      return next;
    });
    if (active === path) {
      setActive(null);
      setDiff(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-bg border border-border rounded-lg shadow-xl w-full max-w-4xl h-[80vh] flex flex-col">
        <div className="flex items-center gap-2 px-4 h-12 border-b border-border">
          <Upload size={16} className="text-muted" />
          <span className="font-medium">Review changes</span>
          <span className="text-xs text-muted">
            {files.length} file{files.length === 1 ? "" : "s"} changed ·{" "}
            {selected.size} selected
          </span>
          <div className="flex-1" />
          <button
            className="p-1.5 rounded hover:bg-surface text-muted hover:text-text"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 min-h-0 flex">
          <div className="w-64 shrink-0 border-r border-border flex flex-col min-h-0">
            <button
              className="flex items-center gap-2 px-3 h-9 border-b border-border text-xs text-muted hover:bg-surface/60"
              onClick={toggleAll}
            >
              {allSelected ? (
                <SquareCheck size={14} />
              ) : (
                <Square size={14} />
              )}
              {allSelected ? "Unselect all" : "Select all"}
            </button>
            <div className="flex-1 overflow-auto py-1">
              {files.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted">No changes.</p>
              )}
              {files.map((f) => {
                const isActive = active === f.path;
                const isSel = selected.has(f.path);
                return (
                  <div
                    key={f.path}
                    className={`flex items-center gap-1 pr-1 pl-2 ${
                      isActive ? "bg-surface" : "hover:bg-surface/60"
                    }`}
                  >
                    <button
                      className="shrink-0 p-1 text-muted hover:text-text"
                      aria-label={isSel ? "Unstage" : "Stage"}
                      onClick={() => toggle(f.path)}
                    >
                      {isSel ? (
                        <SquareCheck size={15} className="text-accent" />
                      ) : (
                        <Square size={15} />
                      )}
                    </button>
                    <button
                      className="flex-1 flex items-center gap-1.5 py-1.5 text-sm text-left min-w-0"
                      onClick={() => void loadDiff(f.path)}
                    >
                      <span
                        className="shrink-0 w-4 text-center text-[10px] font-mono text-amber-500"
                        title={`git: ${f.code.trim() || f.code}`}
                      >
                        {f.code.trim() === "??" ? "U" : f.code.trim() || "M"}
                      </span>
                      <span className="truncate" title={f.path}>
                        {f.path}
                      </span>
                    </button>
                    <button
                      className="shrink-0 p-1 text-muted hover:text-red-500"
                      aria-label={`Discard ${f.path}`}
                      data-tooltip="Discard changes"
                      onClick={() => void discard(f.path)}
                    >
                      <RotateCcw size={13} />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="flex-1 min-w-0 overflow-auto bg-surface/30">
            {loadingDiff ? (
              <div className="h-full flex items-center justify-center text-muted">
                <Loader2 className="animate-spin" size={18} />
              </div>
            ) : !active ? (
              <div className="h-full flex items-center justify-center text-muted text-sm">
                Select a file to preview its changes.
              </div>
            ) : diff?.binary ? (
              <div className="p-6 text-muted text-sm">
                Binary file — diff not shown.
              </div>
            ) : (
              <DiffView text={diff?.diff ?? ""} />
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 px-4 h-14 border-t border-border">
          <input
            className="flex-1 px-3 py-1.5 rounded border border-border bg-bg text-sm outline-none focus:border-accent"
            placeholder="Commit message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
          <button
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent text-white text-sm disabled:opacity-50"
            disabled={pushing || selected.size === 0 || !message.trim()}
            onClick={commit}
          >
            {pushing ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Upload size={14} />
            )}
            Commit &amp; Push ({selected.size})
          </button>
        </div>
      </div>
    </div>
  );
}

function DiffView({ text }: { text: string }): ReactNode {
  if (!text.trim()) {
    return (
      <div className="h-full flex items-center justify-center text-muted text-sm">
        No textual changes.
      </div>
    );
  }
  return (
    <pre className="text-xs font-mono leading-relaxed p-3 whitespace-pre">
      {text.split("\n").map((line, i) => {
        let cls = "text-text";
        if (line.startsWith("+") && !line.startsWith("+++")) {
          cls = "text-green-600 dark:text-green-400 bg-green-500/10";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          cls = "text-red-600 dark:text-red-400 bg-red-500/10";
        } else if (line.startsWith("@@")) {
          cls = "text-accent";
        } else if (
          line.startsWith("diff ") ||
          line.startsWith("index ") ||
          line.startsWith("+++") ||
          line.startsWith("---")
        ) {
          cls = "text-muted";
        }
        return (
          <div key={i} className={`${cls} px-1`}>
            {line || "\u200B"}
          </div>
        );
      })}
    </pre>
  );
}
