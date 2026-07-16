import { useMemo, useState } from "react";
import { Search, SquareKanban } from "lucide-react";
import type { ProjectSummary } from "../lib/types";

interface ProjectListProps {
  projects: ProjectSummary[] | null;
  activeId: string | null;
  error?: string | null;
  onSelect: (project: ProjectSummary) => void;
}

/** Sidebar picker for GitHub Projects v2, mirroring WorkspaceList. */
export function ProjectList({ projects, activeId, error, onSelect }: ProjectListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = projects ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((p) => p.title.toLowerCase().includes(q));
  }, [projects, query]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search projects..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {error ? (
          <div className="px-2 py-4 text-sm text-red-500">{error}</div>
        ) : projects === null ? (
          <div className="px-2 py-4 text-sm text-muted">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">
            {projects.length === 0 ? "No projects found for this repository." : "No matches."}
          </div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((p) => {
              const isActive = p.id === activeId;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(p)}
                    title={p.short_description ?? p.title}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "section-selected" : "hover:bg-surface"
                    }`}
                  >
                    <SquareKanban size={14} className="shrink-0 opacity-70" />
                    <span className="flex-1 truncate">{p.title}</span>
                    <span className="shrink-0 text-xs text-muted">#{p.number}</span>
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
