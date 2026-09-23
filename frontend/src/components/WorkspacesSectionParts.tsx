import { EmptyHero } from "./EmptyHero";
import { WorkspaceList } from "./WorkspaceList";
import { CreateWorkspaceModal, WorkspaceView } from "./WorkspaceView";
import type { WorkspacesController } from "../lib/useWorkspacesController";

// The workspaces section's pieces of the app shell. Each one renders into a
// slot App owns (header, main pane, modal layer, sidebar) and reads its state
// from the workspaces controller.

// The shared header's workspaces branch.
export function WorkspacesHeader({ controller }: { controller: WorkspacesController }) {
  const { activeWorkspace } = controller;
  return (
    <span className="truncate font-medium min-w-0 flex-1">
      {activeWorkspace ? activeWorkspace.name : "Workspaces"}
    </span>
  );
}

// The main pane's workspaces branch.
export function WorkspacesMain({
  controller,
  onSetRole,
}: {
  controller: WorkspacesController;
  onSetRole: (roleId: number | null) => Promise<void>;
}) {
  const {
    workspaces,
    activeWorkspace,
    workspaceInitialPath,
    navigateWorkspace,
    loadWorkspaces,
    setActiveWorkspaceId,
  } = controller;
  return workspaces === null ? (
    <EmptyHero label="Loading workspaces…" />
  ) : activeWorkspace ? (
    <WorkspaceView
      key={activeWorkspace.id}
      workspace={activeWorkspace}
      initialPath={workspaceInitialPath}
      onPathChange={(p) => navigateWorkspace(activeWorkspace.slug, p)}
      onDeleted={async () => {
        const list = await loadWorkspaces();
        const next = list[0] ?? null;
        setActiveWorkspaceId(next?.id ?? null);
        navigateWorkspace(next?.slug ?? null, null);
      }}
      onSetRole={onSetRole}
    />
  ) : (
    <EmptyHero label="No workspaces yet." />
  );
}

// The create dialog, opened by the sidebar "+" in the Files section.
export function WorkspacesCreateModal({ controller }: { controller: WorkspacesController }) {
  const {
    createWorkspaceOpen,
    setCreateWorkspaceOpen,
    loadWorkspaces,
    setActiveWorkspaceId,
    navigateWorkspace,
  } = controller;
  if (!createWorkspaceOpen) return null;
  return (
    <CreateWorkspaceModal
      onClose={() => setCreateWorkspaceOpen(false)}
      onCreated={async (workspace) => {
        setCreateWorkspaceOpen(false);
        await loadWorkspaces();
        setActiveWorkspaceId(workspace.id);
        navigateWorkspace(workspace.slug, null);
      }}
    />
  );
}

// The sidebar's workspaces slot. Named apart from `WorkspaceList`, the list it
// wraps.
export function WorkspacesSidebarList({ controller }: { controller: WorkspacesController }) {
  const { workspaces, activeWorkspaceId, handleSelectWorkspace } = controller;
  return (
    <WorkspaceList
      workspaces={workspaces}
      activeId={activeWorkspaceId}
      onSelect={handleSelectWorkspace}
    />
  );
}
