import { useMemo, useState } from "react";
import { FolderGit2, HardDrive, Plus, Search } from "lucide-react";
import type { Workspace } from "../lib/types";

interface WorkspaceListProps {
  workspaces: Workspace[] | null;
  activeId: number | null;
  onSelect: (workspace: Workspace) => void;
  onCreate: () => void;
}

export function WorkspaceList({ workspaces, activeId, onSelect, onCreate }: WorkspaceListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = workspaces ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((w) => w.name.toLowerCase().includes(q));
  }, [workspaces, query]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search workspaces..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
        <button
          className="rounded p-1.5 hover:bg-surface"
          aria-label="New workspace"
          data-tooltip="New workspace"
          onClick={onCreate}
        >
          <Plus size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {workspaces === null ? (
          <div className="px-2 py-4 text-sm text-muted">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No workspaces yet.</div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((w) => {
              const isActive = w.id === activeId;
              return (
                <li key={w.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(w)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "bg-accent/15 text-accent" : "hover:bg-surface"
                    }`}
                  >
                    {w.kind === "git" ? (
                      <FolderGit2 size={14} className="shrink-0 opacity-70" />
                    ) : (
                      <HardDrive size={14} className="shrink-0 opacity-70" />
                    )}
                    <span className="flex-1 truncate">{w.name}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
