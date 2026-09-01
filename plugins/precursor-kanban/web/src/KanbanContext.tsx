/**
 * Shared state for the kanban section.
 *
 * The section's sidebar (the project picker) and its main pane (the board) are
 * rendered into different subtrees, so their shared state lives in a context
 * mounted by the section's `Provider`. This also owns the section's URL
 * contract — `/kanban/<number>-<slug>#<issue>` — which core hands over as
 * opaque segments plus a hash.
 *
 * Managing *which* boards appear lives here too, rather than only in Settings:
 * adding one is the section's create action, and removing one is a right-click
 * on the row itself. Both write to the plugin's settings blob and then re-list.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { apiErrorMessage } from "@precursor/host";
import type { SectionHost } from "@precursor/host";
import { AddProjectModal } from "./AddProjectModal";
import { kanbanApi, kanbanSettings } from "./api";
import { projectKey } from "./sources";
import type { ProjectSummary } from "./types";

interface KanbanContextValue {
  projects: ProjectSummary[] | null;
  error: string | null;
  /** Opaque ProjectV2 node id of the active board (board fetches need it). */
  activeProjectId: string | null;
  selectProject: (project: ProjectSummary) => void;
  /** Issue/PR number whose preview is open, mirrored in the URL hash. */
  selectedNumber: number | null;
  setSelectedNumber: (n: number | null) => void;
  /** Configured repo, for previewing cards that carry no repo of their own. */
  fallbackRepo: string;
  openTopic: (topicId: number) => void;
  /** Re-run the project listing (after a source or hidden-list change). */
  refreshProjects: () => void;
  /** Take one board out of the picker; reversible from the settings page. */
  hideProject: (project: ProjectSummary) => Promise<void>;
  /** Drop the settings entry a board came from, and every board it brought. */
  stopTracking: (project: ProjectSummary) => Promise<void>;
  /** How many listed boards a given settings entry is responsible for. */
  boardsFromSource: (ref: string) => number;
  /** Why the last hide / stop-tracking failed, if it did. */
  actionError: string | null;
  dismissActionError: () => void;
}

const KanbanContext = createContext<KanbanContextValue | null>(null);

export function useKanban(): KanbanContextValue {
  const ctx = useContext(KanbanContext);
  if (!ctx) throw new Error("useKanban must be used inside the kanban section provider");
  return ctx;
}

/**
 * Bridge from core's header "+" to this section's dialog.
 *
 * `SectionPlugin.onNew` is a plain callback with no handle on the section's
 * React tree — core owns the button, the section owns what it does — so the
 * registration publishes here and the provider, which *is* in the tree,
 * subscribes. A module-scope signal rather than a context because the publisher
 * runs outside any provider.
 */
type AddListener = () => void;
const addListeners = new Set<AddListener>();

/** Called by the section's `onNew`. No-op when the section isn't mounted. */
export function requestAddProject(): void {
  for (const listen of addListeners) listen();
}

function onAddProjectRequested(listener: AddListener): () => void {
  addListeners.add(listener);
  return () => addListeners.delete(listener);
}

/**
 * Human-readable, stable slug for a ProjectV2: its per-owner `number` (the
 * resolvable key) plus a slugified title for readability, e.g. "4-work-ms".
 */
export function projectSlug(project: ProjectSummary): string {
  const base = project.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return base ? `${project.number}-${base}` : String(project.number);
}

/**
 * Resolve a URL segment back to a project number. Only the leading integer is
 * significant, so the trailing title slug is cosmetic and can drift without
 * breaking the link ("4", "4-work-ms" and "4-renamed" all resolve).
 */
function projectRefNumber(ref: string | undefined): number | null {
  if (!ref) return null;
  const match = /^(\d+)/.exec(ref);
  return match ? Number(match[1]) : null;
}

/** Parse a "<number>" hash fragment into an issue/PR number, else null. */
function parseHashNumber(hash: string): number | null {
  const raw = hash.replace(/^#/, "").trim();
  if (!/^\d+$/.test(raw)) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

export function KanbanProvider({
  host,
  children,
}: {
  host: SectionHost;
  children: ReactNode;
}) {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [addOpen, setAddOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const githubRepo = (host.settings?.github_repo ?? "").trim();
  const urlNumber = projectRefNumber(host.segments[0]);
  const hashNumber = parseHashNumber(host.hash);

  const refreshProjects = useCallback(() => setReloadToken((n) => n + 1), []);

  // Core's header "+" lives outside this tree, so it reaches the dialog through
  // the module-scope signal above.
  useEffect(() => onAddProjectRequested(() => setAddOpen(true)), []);

  // Load the owner's boards once the section mounts, again whenever the
  // configured repo changes (a different owner has different projects), and on
  // demand after the source or hidden list is edited.
  useEffect(() => {
    let cancelled = false;
    setProjects(null);
    setError(null);
    kanbanApi
      .listProjects()
      .then((list) => {
        if (!cancelled) setProjects(list);
      })
      .catch((e) => {
        if (!cancelled) {
          setProjects([]);
          setError(apiErrorMessage(e, "Failed to load GitHub projects"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [githubRepo, reloadToken]);

  // The URL is the source of truth for which board is open: resolve its
  // number-based ref against the loaded list. Re-runs when the list arrives, so
  // a deep link entered before the fetch completes still lands on its board.
  // With no (or an unresolvable) ref, fall back to the first board so entering
  // the section always shows something.
  useEffect(() => {
    if (projects === null) return;
    const match = urlNumber != null ? projects.find((p) => p.number === urlNumber) : undefined;
    setActiveProjectId((current) => {
      if (match) return match.id;
      const kept = current && projects.some((p) => p.id === current) ? current : null;
      return kept ?? projects[0]?.id ?? null;
    });
  }, [urlNumber, projects]);

  // Keep the URL in step with the selection. `navigate` replaces rather than
  // pushes: picking a board isn't a separate history entry from the click that
  // caused it, and the hash carries the open card.
  const active = useMemo(
    () => projects?.find((p) => p.id === activeProjectId) ?? null,
    [projects, activeProjectId],
  );
  const lastUrl = useRef<string | null>(null);
  useEffect(() => {
    // Until the list resolves there is nothing to normalise against, and
    // rewriting now would drop a deep link's project/card refs.
    if (projects === null) return;
    const segments = active ? [projectSlug(active)] : [];
    const hash = hashNumber != null && active ? String(hashNumber) : "";
    const key = `${segments.join("/")}#${hash}`;
    if (lastUrl.current === key) return;
    lastUrl.current = key;
    host.navigate(segments, hash);
  }, [active, hashNumber, host, projects]);

  const selectProject = useCallback(
    (project: ProjectSummary) => {
      setActiveProjectId(project.id);
      // Switching boards drops any hash-selected card from the old one.
      host.navigate([projectSlug(project)], "", { push: true });
    },
    [host],
  );

  const setSelectedNumber = useCallback(
    (n: number | null) => {
      // Opening a card is a navigation, so Back closes the preview instead of
      // leaving the board. Clearing it replaces, since Back has just undone it.
      host.navigate(host.segments, n != null ? String(n) : "", { push: n != null });
    },
    [host],
  );

  const hideProject = useCallback(
    async (project: ProjectSummary) => {
      // Every board has an owner in practice, but the field is nullable and the
      // hidden list is keyed on it — without one there is nothing to store.
      if (!project.owner) return;
      // The context menu invokes this fire-and-forget, so a rejection here would
      // otherwise close the menu, skip the refresh and leave the row untouched
      // with no hint that anything went wrong.
      try {
        await kanbanSettings.hideProject(projectKey(project.owner, project.number));
      } catch (e) {
        setActionError(apiErrorMessage(e, `Failed to hide "${project.title}"`));
        return;
      }
      setActionError(null);
      refreshProjects();
    },
    [refreshProjects],
  );

  const stopTracking = useCallback(
    async (project: ProjectSummary) => {
      // Only boards an explicit entry produced; the configured repo's own are
      // implicit and can be hidden but not untracked.
      if (!project.source_ref) return;
      try {
        await kanbanSettings.removeSource(project.source_ref);
      } catch (e) {
        setActionError(apiErrorMessage(e, `Failed to stop tracking ${project.source_ref}`));
        return;
      }
      setActionError(null);
      refreshProjects();
    },
    [refreshProjects],
  );

  const boardsFromSource = useCallback(
    (ref: string) => (projects ?? []).filter((p) => p.source_ref === ref).length,
    [projects],
  );

  const dismissActionError = useCallback(() => setActionError(null), []);

  const value = useMemo<KanbanContextValue>(
    () => ({
      projects,
      error,
      activeProjectId,
      selectProject,
      selectedNumber: hashNumber,
      setSelectedNumber,
      fallbackRepo: githubRepo,
      openTopic: host.openTopic,
      refreshProjects,
      hideProject,
      stopTracking,
      boardsFromSource,
      actionError,
      dismissActionError,
    }),
    [
      projects,
      error,
      activeProjectId,
      selectProject,
      hashNumber,
      setSelectedNumber,
      githubRepo,
      host.openTopic,
      refreshProjects,
      hideProject,
      stopTracking,
      boardsFromSource,
      actionError,
      dismissActionError,
    ],
  );

  return (
    <KanbanContext.Provider value={value}>
      {children}
      {addOpen && (
        <AddProjectModal onClose={() => setAddOpen(false)} onAdded={refreshProjects} />
      )}
    </KanbanContext.Provider>
  );
}
