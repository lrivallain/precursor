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
import { EmptyHero, registerSection, registerSettingsPage } from "@precursor/host";
import type { SectionHost } from "@precursor/host";
import { KanbanBoard } from "./KanbanBoard";
import { KanbanProvider, useKanban } from "./KanbanContext";
import { KanbanSettings } from "./KanbanSettings";
import { ProjectList } from "./ProjectList";

/** Must match `precursor_kanban.plugin.SECTION_ID`. */
export const KANBAN_SECTION_ID = "kanban";

function KanbanSidebar() {
  const { projects, activeProjectId, error, selectProject } = useKanban();
  return (
    <ProjectList
      projects={projects}
      activeId={activeProjectId}
      error={error}
      onSelect={selectProject}
    />
  );
}

function KanbanMain() {
  const {
    projects,
    error,
    activeProjectId,
    selectedNumber,
    setSelectedNumber,
    fallbackRepo,
    openTopic,
  } = useKanban();

  if (projects === null) return <EmptyHero label="Loading projects…" />;
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
  if (projects.length === 0) {
    return <EmptyHero label="No GitHub projects found for this repository." />;
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
  // The board reads GitHub Projects v2 through the issue surface, so it needs
  // both a configured repo and issue associations turned on. Saying which one is
  // missing matters: without it, installing and enabling the plugin appears to
  // do nothing at all.
  unavailable: ({ settings }) => {
    if (settings == null) return "Loading settings…";
    if (!(settings.issue_associations_enabled ?? true)) {
      return "GitHub issue associations are turned off (Settings → GitHub).";
    }
    if ((settings.github_repo ?? "").trim().length === 0) {
      return "No GitHub repository is configured (Settings → GitHub).";
    }
    return null;
  },
  Provider: ({ host, children }: { host: SectionHost; children: React.ReactNode }) => (
    <KanbanProvider host={host}>{children}</KanbanProvider>
  ),
  Sidebar: KanbanSidebar,
  Main: KanbanMain,
  Title: KanbanTitle,
});

// Settings → Plugins → Kanban: extra project sources beyond the configured repo.
registerSettingsPage({
  id: KANBAN_SECTION_ID,
  label: "Kanban",
  icon: SquareKanban,
  Component: KanbanSettings,
});
