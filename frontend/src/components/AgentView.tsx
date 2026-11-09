import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useAgentLookups } from "../lib/useAgentLookups";
import type { PendingEcho } from "../lib/useAgentPendingEcho";
import { useAgentRuntime } from "../lib/useAgentRuntime";
import { useAgentComposerInfra, useAgentStartForm } from "../lib/useAgentStartForm";
import { useBusyAction } from "../lib/useBusyAction";
import type { AgentSession } from "../lib/types";
import { AgentCockpit } from "./agent/AgentCockpit";
import { AgentRuntimeSetup, AgentsDisabled } from "./agent/AgentGates";
import { AgentStartForm } from "./agent/AgentStartForm";
import { AgentsRuntimeCard } from "./AgentsRuntimeCard";
import { SectionLoading } from "./SectionLoading";
import { TopicPicker } from "./TopicPicker";

// The read-only timeline and the topic lookup are imported from here by the
// workflow trace and the agent settings panel.
export { AgentActivity } from "./agent/AgentActivity";
// Extracted to ./TopicPicker so the Live meeting session picker can reuse it.
export { TopicPicker };

interface AgentViewProps {
  agents: AgentSession[];
  agentId: number | null;
  enabled: boolean;
  /**
   * Feature state (and the session list) not resolved yet. Held apart from
   * `enabled` because settings load asynchronously: without it a cold open of
   * the agents surface flashes "Agents mode is off" before the fleet appears.
   */
  loading?: boolean;
  available: boolean;
  runtimeStarted: boolean;
  /** Re-fetch the session list in the parent (status changed, created, deleted). */
  onReload: () => void;
  /** Select a session (or clear with null to show the start form). */
  onSelect: (id: number | null) => void;
  onOpenSettings: () => void;
  /** Topic to preselect in the new-agent form (e.g. opened from a topic). */
  draftTopicId?: number | null;
  /** Persist a role change for the open agent (null = default). */
  onSetRole?: (roleName: string | null) => Promise<void>;
}

/**
 * The Agents surface: the runtime setup, the new-agent form, or the cockpit of
 * the selected agent (components/agent/).
 */
export function AgentView({
  agents,
  agentId,
  enabled,
  loading = false,
  available,
  runtimeStarted,
  onReload,
  onSelect,
  onOpenSettings,
  draftTopicId,
  onSetRole,
}: AgentViewProps) {
  const needsRuntime = !available || (enabled && !runtimeStarted);
  const runtimeState = useAgentRuntime(!loading && needsRuntime);
  const selected = useMemo(() => agents.find((a) => a.id === agentId) ?? null, [agents, agentId]);
  const { topics, collections, persona } = useAgentLookups(enabled);
  const action = useBusyAction();
  const [pending, setPending] = useState<PendingEcho | null>(null);
  // Drop an echo when leaving the agent it targeted (it survives the switch
  // *into* its own freshly created session, cleared later when it lands).
  useEffect(() => {
    setPending((p) => (p && p.agentId !== agentId ? null : p));
  }, [agentId]);
  const [followUp, setFollowUp] = useState("");
  const [roleOpen, setRoleOpen] = useState(false);
  const form = useAgentStartForm(agentId, draftTopicId);
  const composer = useAgentComposerInfra(selected != null, form.setTask, setFollowUp);

  async function startTask(): Promise<void> {
    if (action.busy || !form.task.trim()) return;
    const message = form.task.trim();
    await action.run(async () => {
      const created = await api.agents.create({
        task: message,
        topic_id: form.topicId,
        role: form.role,
        autonomy_enabled: form.autonomy,
        max_steps: form.maxSteps,
        approval_policy: form.approvalPolicy || null,
        start: form.start,
      });
      // Echo the prompt into the new session's transcript right away (only when
      // it's actually starting) so the hand-off feels immediate while the
      // runtime spins the agent up. A parked (waiting) agent has no live turn.
      if (form.start) setPending({ agentId: created.id, text: message });
      form.reset();
      onReload();
      onSelect(created.id);
    });
  }

  // Still resolving whether the feature is on (and what's in the fleet): stay
  // neutral rather than claiming it's off or offering an empty start form.
  if (loading) return <SectionLoading label="Loading agents…" />;

  const runtimeCard = (
    <AgentsRuntimeCard
      status={runtimeState.runtime}
      enabled={enabled}
      loading={runtimeState.loading}
      error={runtimeState.error}
      onRefresh={runtimeState.refresh}
      onUpdate={runtimeState.update}
    />
  );

  if (!selected && needsRuntime) {
    return <AgentRuntimeSetup available={available} runtimeCard={runtimeCard} onOpenSettings={onOpenSettings} />;
  }
  if (!selected && !enabled) return <AgentsDisabled onOpenSettings={onOpenSettings} />;
  if (!selected) {
    return (
      <AgentStartForm
        form={form}
        composer={composer}
        topics={topics}
        collections={collections}
        disabled={!available || action.busy}
        error={action.error}
        roleOpen={roleOpen}
        onRoleOpenChange={setRoleOpen}
        onSubmit={() => void startTask()}
      />
    );
  }
  return (
    <AgentCockpit
      agent={selected}
      enabled={enabled}
      needsRuntime={needsRuntime}
      runtimeReady={enabled && available && runtimeStarted}
      runtimeCard={runtimeCard}
      onOpenSettings={onOpenSettings}
      onReload={onReload}
      onSetRole={onSetRole}
      action={action}
      pending={pending}
      setPending={setPending}
      followUp={followUp}
      setFollowUp={setFollowUp}
      composer={composer}
      roleOpen={roleOpen}
      setRoleOpen={setRoleOpen}
      user={persona}
    />
  );
}
