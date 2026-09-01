import { useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Eye,
  EyeOff,
  Search,
  SquareKanban,
  Trash2,
  X,
} from "lucide-react";
import { ContextMenu, useConfirm, useScrollActiveIntoView } from "@precursor/host";
import type { ContextMenuItem } from "@precursor/host";
import type { ProjectSummary, UnresolvedSource } from "./types";

interface ProjectListProps {
  projects: ProjectSummary[] | null;
  /** Configured sources that produce no board of their own. */
  unresolved: UnresolvedSource[];
  activeId: string | null;
  error?: string | null;
  onSelect: (project: ProjectSummary) => void;
  onHide: (project: ProjectSummary) => Promise<void>;
  onShow: (project: ProjectSummary) => Promise<void>;
  onStopTracking: (ref: string) => Promise<void>;
  /** How many listed boards share a settings entry, for the confirm copy. */
  boardsFromSource: (ref: string) => number;
  /** Why the last context-menu action failed, if it did. */
  actionError?: string | null;
  onDismissActionError: () => void;
  /** Shown when there are no projects at all. */
  emptyLabel?: string;
}

/**
 * Sidebar picker for GitHub Projects v2, mirroring WorkspaceList.
 *
 * This is the *only* surface for managing which boards are tracked, so it has
 * to show more than the boards that are working: a hidden board and a source
 * that resolves to nothing would otherwise be invisible **and** unremovable.
 * Both get a muted group of their own rather than a trip to Settings.
 */
export function ProjectList({
  projects,
  unresolved,
  activeId,
  error,
  onSelect,
  onHide,
  onShow,
  onStopTracking,
  boardsFromSource,
  actionError,
  onDismissActionError,
  emptyLabel = "No projects found for this repository.",
}: ProjectListProps) {
  const [query, setQuery] = useState("");
  const [showHidden, setShowHidden] = useState(false);
  const [menu, setMenu] = useState<
    { kind: "project"; project: ProjectSummary; x: number; y: number } |
    { kind: "source"; source: UnresolvedSource; x: number; y: number } |
    null
  >(null);
  const activeItemRef = useScrollActiveIntoView<HTMLButtonElement>(activeId);
  const confirm = useConfirm();

  const matches = useMemo(() => {
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

  const visible = useMemo(() => matches.filter((p) => !p.hidden), [matches]);
  const hidden = useMemo(() => matches.filter((p) => p.hidden), [matches]);

  // Only worth labelling boards by account when more than one is in play.
  const multipleOwners = useMemo(
    () => new Set((projects ?? []).map((p) => p.owner).filter(Boolean)).size > 1,
    [projects],
  );

  /** Confirm, then drop a settings entry and every board it brought. */
  async function confirmStopTracking(ref: string): Promise<void> {
    const shared = boardsFromSource(ref);
    const ok = await confirm({
      title: "Stop tracking this source?",
      message:
        shared > 1
          ? `"${ref}" is an account, so removing it takes all ${shared} of its boards out of the picker. You can add it again later.`
          : `"${ref}" will no longer be listed. You can add it again later.`,
      confirmLabel: "Stop tracking",
      variant: "danger",
    });
    if (ok) await onStopTracking(ref);
  }

  /**
   * Actions for one board.
   *
   * Hide/show always means exactly this board — including the ones the
   * configured repository's owner provides, which no settings entry produced.
   * Untracking is only offered for boards an entry *did* produce, and says up
   * front when removing that entry takes other boards with it.
   */
  function itemsForProject(project: ProjectSummary): ContextMenuItem[] {
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
    items.push(
      project.hidden
        ? { label: "Show on board", icon: Eye, onSelect: () => onShow(project) }
        : { label: "Hide from board", icon: EyeOff, onSelect: () => onHide(project) },
    );
    const ref = project.source_ref;
    if (ref) {
      const shared = boardsFromSource(ref);
      items.push({
        label: shared > 1 ? `Stop tracking ${ref} (${shared} boards)` : `Stop tracking ${ref}`,
        icon: Trash2,
        danger: true,
        onSelect: () => confirmStopTracking(ref),
      });
    }
    return items;
  }

  function itemsForSource(source: UnresolvedSource): ContextMenuItem[] {
    return [
      {
        label: `Stop tracking ${source.ref}`,
        icon: Trash2,
        danger: true,
        onSelect: () => confirmStopTracking(source.ref),
      },
    ];
  }

  function renderProject(p: ProjectSummary) {
    const isActive = p.id === activeId;
    return (
      <li key={p.id}>
        <button
          type="button"
          ref={isActive ? activeItemRef : undefined}
          onClick={() => onSelect(p)}
          onContextMenu={(event) => {
            event.preventDefault();
            setMenu({ kind: "project", project: p, x: event.clientX, y: event.clientY });
          }}
          title={p.short_description ?? (p.owner ? `${p.owner} / ${p.title}` : p.title)}
          className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
            isActive ? "section-selected" : "hover:bg-surface"
          } ${p.hidden ? "opacity-60" : ""}`}
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
  }

  const nothingAtAll =
    projects !== null && projects.length === 0 && unresolved.length === 0;

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

      {/* A failed hide / show / stop-tracking leaves the row exactly where it
          was, so without this the action would look like it simply did nothing. */}
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
        ) : nothingAtAll ? (
          <div className="px-2 py-4 text-sm text-muted">{emptyLabel}</div>
        ) : visible.length === 0 && hidden.length === 0 && unresolved.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No matches.</div>
        ) : (
          <>
            {visible.length > 0 && <ul className="space-y-0.5">{visible.map(renderProject)}</ul>}

            {/* Hidden boards are still tracked, so the only way back is from
                here — dropping them from the list entirely would make hiding a
                one-way door. */}
            {hidden.length > 0 && (
              <div className="mt-2 border-t border-border pt-2">
                <button
                  type="button"
                  onClick={() => setShowHidden((v) => !v)}
                  aria-expanded={showHidden}
                  className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[11px] font-medium text-muted hover:bg-surface"
                >
                  {showHidden ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  Hidden ({hidden.length})
                </button>
                {showHidden && <ul className="mt-0.5 space-y-0.5">{hidden.map(renderProject)}</ul>}
              </div>
            )}

            {/* A source that resolves to no board would otherwise be invisible
                and therefore impossible to remove. */}
            {unresolved.length > 0 && (
              <div className="mt-2 border-t border-border pt-2">
                <p className="px-2 py-1 text-[11px] font-medium text-muted">Not resolving</p>
                <ul className="space-y-0.5">
                  {unresolved.map((s) => (
                    <li key={s.ref}>
                      <button
                        type="button"
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setMenu({
                            kind: "source",
                            source: s,
                            x: event.clientX,
                            y: event.clientY,
                          });
                        }}
                        title={
                          s.kind === "pinned"
                            ? "That project is gone, private, or your token can't see it."
                            : "That account has no visible open projects."
                        }
                        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm text-muted hover:bg-surface"
                      >
                        <AlertTriangle
                          size={14}
                          className="shrink-0 text-amber-600 dark:text-amber-400"
                        />
                        <span className="min-w-0 flex-1 truncate font-mono text-xs">{s.ref}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>

      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label={
            menu.kind === "project"
              ? `Actions for ${menu.project.title}`
              : `Actions for ${menu.source.ref}`
          }
          onClose={() => setMenu(null)}
          items={
            menu.kind === "project"
              ? itemsForProject(menu.project)
              : itemsForSource(menu.source)
          }
        />
      )}
    </div>
  );
}
