import { followUpKind } from "./agentCockpitModel";
import { api } from "./api";
import type { BusyAction } from "./useBusyAction";
import type { PendingEcho } from "./useAgentPendingEcho";
import type { AgentPermissionDecisionValue } from "./types";

export interface CockpitActionsOptions {
  agentId: number;
  action: BusyAction;
  /** A rewind is being applied: no new prompt meanwhile. */
  rewindBusy: boolean;
  followUp: string;
  setFollowUp: (text: string) => void;
  setPending: (pending: PendingEcho | null) => void;
  openRolePicker: () => void;
  /** Hand the pane back to the agent: a new turn starts. */
  followTurn: () => void;
  onReload: () => void;
}

export interface CockpitActions {
  /** Send the composer's draft, or `explicit` (a suggestion, /compact). */
  sendFollowUp: (explicit?: string) => Promise<void>;
  stop: () => void;
  resume: () => void;
  startNow: () => void;
  approve: (requestId: string, decision: AgentPermissionDecisionValue) => Promise<void>;
}

/** What the open agent's controls do. Each reloads the session list after. */
export function useAgentCockpitActions(opts: CockpitActionsOptions): CockpitActions {
  const { agentId, action, onReload, setPending, setFollowUp } = opts;
  const call = (fn: () => Promise<unknown>) => () =>
    void action.run(async () => {
      await fn();
      onReload();
    });

  async function sendFollowUp(explicit?: string): Promise<void> {
    if (action.busy || opts.rewindBusy) return;
    const message = (explicit ?? opts.followUp).trim();
    if (!message) return;
    const fromDraft = explicit === undefined;
    const kind = followUpKind(message);
    // /clear erases the whole transcript on the backend — confirm first,
    // mirroring the topic/chat clear flow.
    if (kind === "clear" && !window.confirm("Clear this agent conversation? The transcript will be erased.")) return;
    // Clear the composer and echo the prompt immediately so it's obvious the
    // message left, even before the backend records the real turn.
    if (fromDraft) setFollowUp("");
    // The bare form opens this composer's role picker, the way it does on every
    // other surface; `/role <name>` goes through the backend command handler.
    if (kind === "role") {
      opts.openRolePicker();
      return;
    }
    setPending({ agentId, text: message });
    // A new prompt (not a slash command) starts a turn: follow it from now,
    // rather than from when the backend reports it running.
    if (kind === "prompt") opts.followTurn();
    const sent = await action.run(async () => {
      await api.agents.send(agentId, message);
      // A slash command runs server-side and never lands as a user turn, so its
      // echo would otherwise linger; the command has finished by now.
      if (kind !== "prompt") setPending(null);
      onReload();
    });
    if (sent) return;
    setPending(null);
    // Restore the draft so the user doesn't lose what they typed.
    if (fromDraft) setFollowUp(message);
  }

  return {
    sendFollowUp,
    stop: call(() => api.agents.cancel(agentId)),
    resume: call(() => api.agents.resume(agentId)),
    startNow: call(() => api.agents.start(agentId)),
    approve: async (requestId, decision) => {
      await action.run(
        async () => {
          await api.agents.resolvePermission(agentId, requestId, decision);
          onReload();
        },
        { clearError: false },
      );
    },
  };
}
