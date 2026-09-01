import { useMemo, useState } from "react";
import { ExternalLink, EyeOff, Search, SquareKanban, Trash2, X } from "lucide-react";
import { ContextMenu, useConfirm, useScrollActiveIntoView } from "@precursor/host";
import type { ContextMenuItem } from "@precursor/host";
import type { ProjectSummary } from "./types";

interface ProjectListProps {
  projects: ProjectSummary[] | null;
  activeId: string | null;
  error?: string | null;
  onSelect: (project: ProjectSummary) => void;
  onHide: (project: ProjectSummary) => Promise<void>;
  onStopTracking: (project: ProjectSummary) => Promise<void>;
  /** How many listed boards share a settings entry, for the confirm copy. */
  boardsFromSource: (ref: string) => number;
  /** Why the last context-menu action failed, if it did. */
  actionError?: string | null;
  onDismissActionError: () => void;
}

/** Sidebar picker for GitHub Projects v2, mirroring WorkspaceList. */
export function ProjectList({
  projects,
  activeId,
  error,
  onSelect,
  onHide,
  onStopTracking,
  boardsFromSource,
  actionError,
  onDismissActionError,
}: ProjectListProps) {
  const [query, setQuery] = useState("");
  const [menu, setMenu] = useState<{ project: ProjectSummary; x: number; y: number } | null>(null);
  const activeItemRef = useScrollActiveIntoView<HTMLButtonElement>(activeId);
  const confirm = useConfirm();

  const filtered = useMemo(() => {
    const list = projects ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    // Search the owner too: with boards from several accounts, "acme" is the
    // natural way to narrow to a customer's.
    return list.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        (p.owner ?? "").toLowerCase().includes(q),
    );
  }, [projects, query]);

  // Only worth labelling boards by account when more than one is in play.
  const multipleOwners = useMemo(
    () => new Set((projects ?? []).map((p) => p.owner).filter(Boolean)).size > 1,
    [projects],
  );

  /**
   * Actions for one board.
   *
   * Hiding is always available and always means exactly this board — including
   * the ones the configured repo's owner provides, which no settings entry
   * produced and which were otherwise impossible to get out of the way.
   * Untracking is only offered for boards an entry *did* produce, and says up
   * front when removing that entry takes other boards with it.
   */
  function itemsFor(project: ProjectSummary): ContextMenuItem[] {
    const items: ContextMenuItem[] = [];
    const url = project.url;
    if (url) {
      items.push({
        label: "Open on GitHub",
        icon: ExternalLink,
        onSelect: () => {
          window.open(url, "_blank", "noopener,noreferrer");
        },
      });
    }
    items.push({
      label: "Hide from board",
      icon: EyeOff,
      onSelect: () => onHide(project),
    });
    const ref = project.source_ref;
    if (ref) {
      const shared = boardsFromSource(ref);
      items.push({
        label: shared > 1 ? `Stop tracking ${ref} (${shared} boards)` : `Stop tracking ${ref}`,
        icon: Trash2,
        danger: true,
        onSelect: async () => {
          const ok = await confirm({
            title: "Stop tracking this source?",
            message:
              shared > 1
                ? `"${ref}" is an account, so removing it takes all ${shared} of its boards out of the picker. You can add it again later.`
                : `"${ref}" will no longer be listed. You can add it again later.`,
            confirmLabel: "Stop tracking",
            variant: "danger",
          });
          if (ok) await onStopTracking(project);
        },
      });
    }
    return items;
  }

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

      {/* A failed hide / stop-tracking leaves the row exactly where it was, so
          without this the action would look like it simply did nothing. */}
      {actionError && (
        <div className="flex items-start gap-2 border-b border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-500">
          <span className="min-w-0 flex-1">{actionError}</span>
          <button
            type="button"
            onClick={onDismissActionError}
            className="shrink-0 rounded p-0.5 hover:bg-red-500/10"
            aria-label="Dismiss error"
            data-tooltip="Dismiss"
          >
            <X size={13} />
          </button>
        </div>
      )}

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
                    ref={isActive ? activeItemRef : undefined}
                    onClick={() => onSelect(p)}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      setMenu({ project: p, x: event.clientX, y: event.clientY });
                    }}
                    title={p.short_description ?? (p.owner ? `${p.owner} / ${p.title}` : p.title)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "section-selected" : "hover:bg-surface"
                    }`}
                  >
                    <SquareKanban size={14} className="shrink-0 opacity-70" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate">{p.title}</span>
                      {multipleOwners && p.owner && (
                        <span className="truncate text-[11px] text-muted">{p.owner}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-muted">#{p.number}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label={`Actions for ${menu.project.title}`}
          onClose={() => setMenu(null)}
          items={itemsFor(menu.project)}
        />
      )}
    </div>
  );
}
