/**
 * Kanban — the frontend half of the `precursor-kanban` plugin.
 *
 * The section only appears when the Python package is installed (it publishes
 * the matching `section` descriptor at `/api/plugins`) *and* a GitHub repo is
 * configured. Everything the section needs — palette, icon, routing, state,
 * HTTP client — lives in this folder; core knows it only through the contract
 * in `lib/plugins.ts`.
 */

import { SquareKanban } from "lucide-react";
import { EmptyHero, registerSection } from "@precursor/host";
import type { SectionHost } from "@precursor/host";
import { KanbanBoard } from "./KanbanBoard";
import { KanbanProvider, requestAddProject, useKanban } from "./KanbanContext";
import { ProjectList } from "./ProjectList";

/** Must match `precursor_kanban.plugin.SECTION_ID`. */
export const KANBAN_SECTION_ID = "kanban";

/**
 * What to show before the board can work.
 *
 * The section no longer hides itself when GitHub isn't set up — an installed,
 * enabled plugin that is nowhere to be seen reads as broken. So the setup step
 * is explained here, in the place the user actually lands, instead of the raw
 * guard error the API would otherwise surface.
 *
 * A configured repository is *not* part of that: it only contributes its
 * owner's boards, so an install driven entirely by added projects is complete
 * without one. `hasProjects` is what tells "nothing set up yet" apart from
 * "set up, and everything is hidden".
 */
function setupHint(
  settings: SectionHost["settings"],
  hasProjects: boolean,
): string | null {
  if (settings == null) return null;
  if (!(settings.issue_associations_enabled ?? true)) {
    return "GitHub issue associations are turned off. Enable them in Settings → GitHub to use the board.";
  }
  if (hasProjects) return null;
  if ((settings.github_repo ?? "").trim().length === 0) {
    return "Add a project with + above, or connect a GitHub repository in Settings → GitHub to list its projects here.";
  }
  return null;
}

function KanbanSidebar({ host }: { host: SectionHost }) {
  const {
    projects,
    unresolved,
    activeProjectId,
    error,
    selectProject,
    hideProject,
    showProject,
    stopTracking,
    boardsFromSource,
    actionError,
    dismissActionError,
  } = useKanban();

  // Nothing tracked and no repo: the listing is empty by definition, so the
  // picker stays quiet and the main pane does the explaining.
  const pendingSetup =
    setupHint(host.settings, (projects?.length ?? 0) > 0 || unresolved.length > 0) !== null;

  return (
    <ProjectList
      projects={pendingSetup ? [] : projects}
      unresolved={pendingSetup ? [] : unresolved}
      activeId={activeProjectId}
      error={pendingSetup ? null : error}
      emptyLabel={pendingSetup ? "No projects tracked yet." : undefined}
      onSelect={selectProject}
      onHide={hideProject}
      onShow={showProject}
      onStopTracking={stopTracking}
      boardsFromSource={boardsFromSource}
      actionError={actionError}
      onDismissActionError={dismissActionError}
    />
  );
}

function KanbanMain({ host }: { host: SectionHost }) {
  const {
    projects,
    unresolved,
    error,
    activeProjectId,
    selectedNumber,
    setSelectedNumber,
    fallbackRepo,
    openTopic,
  } = useKanban();

  // Checked before the fetch state: with nothing tracked the board has nothing
  // to draw, and saying so in setup terms beats an empty list.
  if (projects === null) return <EmptyHero label="Loading projects…" />;
  const hint = setupHint(host.settings, projects.length > 0 || unresolved.length > 0);
  if (hint) return <EmptyHero label={hint} />;

  if (error) return <EmptyHero label={error} />;
  if (activeProjectId) {
    return (
      <KanbanBoard
        key={activeProjectId}
        projectId={activeProjectId}
        fallbackRepo={fallbackRepo}
        selectedNumber={selectedNumber}
        onSelectedNumberChange={setSelectedNumber}
        onOpenTopic={openTopic}
      />
    );
  }
  // Every board hidden is a deliberate state, not an empty one — say which,
  // otherwise "no projects" looks like the tracked sources stopped working.
  if (projects.length > 0) {
    return <EmptyHero label="Every tracked board is hidden. Show one from the picker." />;
  }
  return <EmptyHero label="Select a project to view its board." />;
}

function KanbanTitle() {
  const { projects, activeProjectId } = useKanban();
  return <>{projects?.find((p) => p.id === activeProjectId)?.title ?? "Kanban"}</>;
}

registerSection({
  id: KANBAN_SECTION_ID,
  label: "Kanban",
  icon: SquareKanban,
  description: "Track linked issues on a board across your projects.",
  openLabel: "Open board",
  keywords: "kanban board issues tracking projects",
  colors: {
    icon: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
    activeCard: "border-cyan-500/60 bg-cyan-500/10",
    hoverCard: "hover:border-cyan-500/50 hover:bg-cyan-500/5",
    primaryBtn:
      "bg-cyan-500/15 text-cyan-700 hover:bg-cyan-500/25 dark:text-cyan-300 border border-cyan-500/30",
    accentText: "text-cyan-600 dark:text-cyan-400",
    activeTab: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
    hoverTab: "hover:bg-cyan-500/10 hover:text-cyan-600 dark:hover:text-cyan-400",
  },
  accent: { light: "#0891b2", dark: "#22d3ee" },
  // The board's "New" is "track another project". It opens the section's own
  // dialog rather than routing to Settings: adding a board is the section's
  // primary create action, and a trip to a settings tab is a long way round for
  // it. The settings page keeps the full list, including entries that resolve
  // to no board and so have no row here to right-click.
  newLabel: "Add a project",
  onNew: () => requestAddProject(),
  Provider: ({ host, children }: { host: SectionHost; children: React.ReactNode }) => (
    <KanbanProvider host={host}>{children}</KanbanProvider>
  ),
  Sidebar: KanbanSidebar,
  Main: KanbanMain,
  Title: KanbanTitle,
});
