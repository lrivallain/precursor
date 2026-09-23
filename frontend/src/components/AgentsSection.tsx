import {
  ArrowUpRight,
  Archive as ArchiveIcon,
  ChevronLeft,
  Loader2,
  MessagesSquare,
  Play,
  Settings as SettingsIcon,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { AgentDashboard } from "./AgentDashboard";
import { AgentList } from "./AgentList";
import { AgentSettingsPanel } from "./AgentSettingsPanel";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { AgentView } from "./AgentView";
import { InlineTitle } from "./InlineTitle";
import { findTitle } from "../lib/topicTree";
import type { TopicNode } from "../lib/types";
import type { AgentsController } from "../lib/useAgentsController";

// The agents section's pieces of the app shell. Each one renders into a slot
// App owns (header, banner row, main pane, modal layer, sidebar, home launcher)
// and reads its state from the agents controller.

// The shared header's agents branch. App also falls back to it for any mode
// without a header of its own.
export function AgentsHeader({
  controller,
  tree,
  onOverview,
  onOpenTopic,
}: {
  controller: AgentsController;
  tree: TopicNode[];
  onOverview: () => void;
  onOpenTopic: (topicId: number) => void;
}) {
  const {
    agents,
    activeAgent,
    agentComposerOpen,
    setAgentComposerOpen,
    setAgentSettingsOpen,
    startingAgentIds,
    agentRunDisabledReason,
    handleRenameAgent,
    handleRunAgent,
    handleStopAgent,
    handleArchiveAgents,
    handleDeleteAgent,
  } = controller;
  return (
    <>
      {activeAgent ? (
        <>
          <button
            type="button"
            onClick={() => onOverview()}
            className="group inline-flex shrink-0 items-center gap-1 rounded-md border border-violet-500/40 bg-violet-500/10 px-2 py-1 text-[11px] font-medium text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
            data-tooltip="Back to the agents dashboard"
            aria-label="Back to all agents"
          >
            <ChevronLeft size={14} />
            <span>All agents</span>
          </button>
          <InlineTitle
            title={activeAgent.title}
            onRename={(t) => handleRenameAgent(activeAgent.id, t)}
            className="truncate font-medium min-w-0 flex-1"
            inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
          />
          <AgentStatusBadge status={activeAgent.status} />
          {activeAgent.topic_id != null && (
            <button
              type="button"
              onClick={() => {
                const tid = activeAgent.topic_id;
                if (tid == null) return;
                onOpenTopic(tid);
              }}
              className="group inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[11px] font-medium text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
              data-tooltip="Open the associated topic"
            >
              <MessagesSquare size={12} />
              <span className="max-w-[12rem] truncate">
                {findTitle(tree, activeAgent.topic_id) ?? "Topic"}
              </span>
              <ArrowUpRight
                size={12}
                className="opacity-60 transition group-hover:opacity-100"
              />
            </button>
          )}
          <button
            className="p-2 rounded hover:bg-surface shrink-0"
            aria-label="Agent settings"
            data-tooltip="Agent settings"
            onClick={() => setAgentSettingsOpen(true)}
          >
            <SettingsIcon size={18} />
          </button>
          <button
            type="button"
            className="p-2 rounded hover:bg-surface shrink-0 text-accent disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Run agent"
            aria-busy={startingAgentIds.has(activeAgent.id)}
            data-tooltip={agentRunDisabledReason ?? "Run the agent's saved task"}
            disabled={agentRunDisabledReason !== null}
            onClick={() => void handleRunAgent(activeAgent)}
          >
            {startingAgentIds.has(activeAgent.id)
              ? <Loader2 size={18} className="animate-spin" />
              : <Play size={18} />}
          </button>
          {(activeAgent.status === "running" ||
            activeAgent.status === "pending" ||
            activeAgent.status === "needs_approval") && (
            <button
              className="p-2 rounded hover:bg-surface shrink-0 text-muted hover:text-red-500"
              aria-label="Stop agent"
              data-tooltip="Stop agent"
              onClick={() => void handleStopAgent(activeAgent.id)}
            >
              <Square size={18} />
            </button>
          )}
          <button
            className="p-2 rounded hover:bg-surface shrink-0 text-muted hover:text-foreground"
            aria-label="Archive agent"
            data-tooltip="Archive agent"
            onClick={() => void handleArchiveAgents([activeAgent.id])}
          >
            <ArchiveIcon size={18} />
          </button>
          <button
            className="p-2 rounded hover:bg-surface shrink-0 text-muted hover:text-red-500"
            aria-label="Delete agent"
            data-tooltip="Delete agent"
            onClick={() => void handleDeleteAgent(activeAgent)}
          >
            <Trash2 size={18} />
          </button>
        </>
      ) : agentComposerOpen && (agents?.length ?? 0) > 0 ? (
        <>
          <button
            type="button"
            onClick={() => setAgentComposerOpen(false)}
            className="group inline-flex shrink-0 items-center gap-1 rounded-md border border-violet-500/40 bg-violet-500/10 px-2 py-1 text-[11px] font-medium text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
            data-tooltip="Back to the agents dashboard"
            aria-label="Back to all agents"
          >
            <ChevronLeft size={14} />
            <span>All agents</span>
          </button>
          <span className="truncate font-medium min-w-0 flex-1">
            New agent
          </span>
        </>
      ) : (
        <span className="truncate font-medium min-w-0 flex-1">Agents</span>
      )}
    </>
  );
}

// Shown under the header while agents mode is on screen, for the open agent only.
export function AgentRunErrorBanner({ controller }: { controller: AgentsController }) {
  const { agentRunError, setAgentRunError, activeAgent } = controller;
  if (!agentRunError || agentRunError.agentId !== activeAgent?.id) return null;
  return (
    <div role="alert" className="flex items-center gap-2 border-b border-red-500/30 bg-red-500/10 px-3 py-2 text-[12px] text-red-500">
      <span className="min-w-0 flex-1">Could not run agent: {agentRunError.message}</span>
      <button
        type="button"
        onClick={() => setAgentRunError(null)}
        className="shrink-0 rounded p-1 hover:bg-red-500/10"
        aria-label="Dismiss run error"
        data-tooltip="Dismiss run error"
      >
        <X size={14} />
      </button>
    </div>
  );
}

// The main pane's agents branch. Like the header, App also falls back to it for
// any mode without a main pane of its own.
export function AgentsMain({
  controller,
  onOpenWorkflow,
  onOpenSettings,
  onSetRole,
}: {
  controller: AgentsController;
  onOpenWorkflow: (id: number) => void;
  onOpenSettings: () => void;
  onSetRole: (roleId: number | null) => Promise<void>;
}) {
  const {
    agents,
    agentsEnabled,
    agentsAvailable,
    agentsRuntimeStarted,
    agentsError,
    agentsBooting,
    activeAgentId,
    agentComposerOpen,
    setAgentComposerOpen,
    showWorkflowAgents,
    setShowWorkflowAgents,
    agentDraftTopicId,
    loadAgents,
    openAgent,
  } = controller;
  return agentsEnabled && agentsError && agents === null ? (
    <div role="alert" className="p-6 text-sm">
      <p>{agentsError}</p>
      <button type="button" onClick={() => void loadAgents()} className="mt-3 rounded border border-border px-3 py-1.5 hover:bg-surface">
        Retry
      </button>
    </div>
  ) : agentsEnabled &&
    agentsAvailable &&
    agentsRuntimeStarted &&
    !agentsBooting &&
    activeAgentId == null &&
    !agentComposerOpen ? (
    <AgentDashboard
      agents={agents ?? []}
      showWorkflowAgents={showWorkflowAgents}
      onShowWorkflowAgentsChange={setShowWorkflowAgents}
      onSelect={(id) => void openAgent(id)}
      onNew={() => setAgentComposerOpen(true)}
      onImported={(result) => {
        void loadAgents();
        if (result.agent_id != null) void openAgent(result.agent_id);
      }}
      onOpenWorkflow={(id) => onOpenWorkflow(id)}
    />
  ) : (
    <AgentView
      agents={agents ?? []}
      agentId={activeAgentId}
      enabled={agentsEnabled}
      loading={agentsBooting}
      available={agentsAvailable}
      runtimeStarted={agentsRuntimeStarted}
      onReload={() => void loadAgents()}
      onSelect={(id) => void openAgent(id)}
      onOpenSettings={onOpenSettings}
      draftTopicId={agentDraftTopicId}
      onSetRole={onSetRole}
    />
  );
}

// The home launcher's "New agent" card: the start form, never a selection.
export function AgentsHomeSurface({
  controller,
  onOpenSettings,
}: {
  controller: AgentsController;
  onOpenSettings: () => void;
}) {
  const {
    agents,
    agentsEnabled,
    agentsBooting,
    agentsAvailable,
    agentsRuntimeStarted,
    loadAgents,
    selectAgentFromHome,
  } = controller;
  return (
    <AgentView
      agents={agents ?? []}
      agentId={null}
      enabled={agentsEnabled}
      loading={agentsBooting}
      available={agentsAvailable}
      runtimeStarted={agentsRuntimeStarted}
      onReload={() => void loadAgents()}
      onSelect={selectAgentFromHome}
      onOpenSettings={onOpenSettings}
      draftTopicId={null}
    />
  );
}

export function AgentSettingsModal({
  controller,
  onOpenWorkflow,
}: {
  controller: AgentsController;
  onOpenWorkflow: (workflowId: number) => void;
}) {
  const {
    agentSettingsOpen,
    setAgentSettingsOpen,
    activeAgent,
    activeAgentId,
    setActiveAgentId,
    setAgents,
    loadAgents,
  } = controller;
  if (!(agentSettingsOpen && activeAgent)) return null;
  return (
    <AgentSettingsPanel
      agent={activeAgent}
      onClose={() => setAgentSettingsOpen(false)}
      onSaved={(updated) => {
        setAgents((prev) =>
          (prev ?? []).map((a) => (a.id === updated.id ? updated : a)),
        );
        setAgentSettingsOpen(false);
      }}
      onArchived={() => {
        setAgentSettingsOpen(false);
        if (activeAgentId === activeAgent.id) setActiveAgentId(null);
        void loadAgents();
      }}
      onDeleted={() => {
        setAgentSettingsOpen(false);
        if (activeAgentId === activeAgent.id) setActiveAgentId(null);
        void loadAgents();
      }}
      onOpenWorkflow={(workflowId) => {
        setAgentSettingsOpen(false);
        onOpenWorkflow(workflowId);
      }}
    />
  );
}

// The sidebar's agents slot.
export function AgentsSidebarList({
  controller,
  onOverview,
}: {
  controller: AgentsController;
  onOverview: () => void;
}) {
  const {
    agents,
    showWorkflowAgents,
    setShowWorkflowAgents,
    agentComposerOpen,
    activeAgentId,
    agentsBooting,
    agentsEnabled,
    agentsError,
    loadAgents,
    openAgent,
  } = controller;
  return (
    <AgentList
      agents={agents ?? []}
      showWorkflowAgents={showWorkflowAgents}
      onShowWorkflowAgentsChange={setShowWorkflowAgents}
      activeId={agentComposerOpen ? null : activeAgentId}
      overviewSelected={activeAgentId == null && !agentComposerOpen}
      loading={agentsBooting}
      enabled={agentsEnabled}
      error={agentsEnabled ? agentsError : null}
      onRetry={() => void loadAgents()}
      onOverview={() => onOverview()}
      onSelect={(id) => void openAgent(id)}
    />
  );
}
