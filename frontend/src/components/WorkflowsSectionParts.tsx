import { WorkflowSidebarList } from "./WorkflowSidebarList";
import { WorkflowsSection } from "./WorkflowsSection";
import type { WorkflowsController } from "../lib/useWorkflowsController";

// The workflows section's pieces of the app shell. Each one renders into a slot
// App owns (header, main pane, sidebar) and reads its state from the workflows
// controller.

// The shared header's workflows branch.
export function WorkflowsHeader() {
  return <span className="truncate font-medium min-w-0 flex-1">Workflows</span>;
}

// The main pane's workflows branch.
export function WorkflowsMain({
  controller,
  onOpenSettings,
  onOpenAgent,
}: {
  controller: WorkflowsController;
  onOpenSettings: () => void;
  onOpenAgent: (id: number) => void;
}) {
  const {
    agentsEnabled,
    settingsReady,
    workflowCollection,
    activeWorkflowId,
    workflowEditor,
    setWorkflowEditor,
    activeWorkflowRunSeg,
    setActiveWorkflowRunSeg,
    editWorkflow,
    openWorkflow,
  } = controller;
  return (
    <WorkflowsSection
      enabled={agentsEnabled}
      ready={settingsReady}
      workflows={workflowCollection.workflows}
      loading={workflowCollection.loading}
      error={workflowCollection.error}
      onReload={() => void workflowCollection.reload()}
      onChanged={workflowCollection.upsert}
      onDeleted={workflowCollection.remove}
      activeId={activeWorkflowId}
      editor={workflowEditor}
      onEdit={(id) => editWorkflow(id)}
      onCloseEditor={() => setWorkflowEditor(null)}
      runSeg={activeWorkflowRunSeg}
      onNavigate={(id) => void openWorkflow(id)}
      onRunSegChange={setActiveWorkflowRunSeg}
      onOpenSettings={onOpenSettings}
      onOpenAgent={(id) => onOpenAgent(id)}
    />
  );
}

// The sidebar's workflows slot. Named apart from `WorkflowSidebarList`, the
// list it wraps.
export function WorkflowsSidebarSlot({
  controller,
  onOverview,
}: {
  controller: WorkflowsController;
  onOverview: () => void;
}) {
  const {
    workflowCollection,
    activeWorkflowId,
    workflowEditor,
    settingsReady,
    agentsEnabled,
    openWorkflow,
  } = controller;
  return (
    <WorkflowSidebarList
      workflows={workflowCollection.workflows}
      activeId={activeWorkflowId}
      overviewSelected={activeWorkflowId == null && workflowEditor == null}
      loading={!settingsReady || (agentsEnabled && workflowCollection.loading)}
      enabled={agentsEnabled}
      error={agentsEnabled ? workflowCollection.error : null}
      onRetry={() => void workflowCollection.reload()}
      onOverview={() => onOverview()}
      onSelect={(id) => void openWorkflow(id)}
    />
  );
}
