import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import type { useConfirm } from "../components/ConfirmDialog";
import { api } from "./api";
import { agentCanStart, agentsWaitingCount } from "./agents";
import { coalesce, eventBus } from "./events";
import { notifyIfUnfocused, notifyNow } from "./notifications";
import {
  agentUrl,
  navigate,
  parseAppRoute,
  resolveAgentRef,
  type AppRoute,
} from "./routes";
import type { AgentSession } from "./types";
import { windowFocused } from "./windowFocus";

// Agent statuses that represent a finished/paused turn (not actively running).
// Used to re-mark the actively-viewed agent read once per turn rather than on
// every streamed event.
const AGENT_SETTLED_STATUSES = new Set([
  "idle",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "needs_approval",
]);

export interface AgentRunError {
  agentId: number;
  message: string;
}

// Shell state the agents section reads or drives. The once-registered listeners
// below (`precursor:open-agent`, the event bus) keep the first render's deps, so
// every function passed here must be stable or read current values via refs.
export interface AgentsControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  settingsReady: boolean;
  agentsEnabled: boolean;
  agentsAvailable: boolean;
  agentsRuntimeStarted: boolean;
  agentsUnavailableReason: string | null;
  notificationsEnabledRef: RefObject<boolean>;
  isViewing: (kind: "topic" | "chat" | "agent", id: number) => boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeWsRoute: () => void;
  closeMobileNav: () => void;
  confirmAction: ReturnType<typeof useConfirm>;
  confirmLeaveRecording: () => Promise<boolean>;
}

export interface AgentsController {
  agents: AgentSession[] | null;
  setAgents: Dispatch<SetStateAction<AgentSession[] | null>>;
  showWorkflowAgents: boolean;
  setShowWorkflowAgents: Dispatch<SetStateAction<boolean>>;
  agentsError: string | null;
  startingAgentIds: Set<number>;
  agentRunError: AgentRunError | null;
  setAgentRunError: Dispatch<SetStateAction<AgentRunError | null>>;
  activeAgentId: number | null;
  setActiveAgentId: Dispatch<SetStateAction<number | null>>;
  activeAgentIdRef: RefObject<number | null>;
  agentDraftTopicId: number | null;
  agentComposerOpen: boolean;
  setAgentComposerOpen: Dispatch<SetStateAction<boolean>>;
  agentSettingsOpen: boolean;
  setAgentSettingsOpen: Dispatch<SetStateAction<boolean>>;
  // Settings-derived flags, passed through so the section components read them
  // from one place.
  agentsEnabled: boolean;
  agentsAvailable: boolean;
  agentsRuntimeStarted: boolean;
  activeAgent: AgentSession | null;
  agentsBooting: boolean;
  agentRunDisabledReason: string | null;
  agentsUnread: number;
  agentsWaiting: number;
  loadAgents: () => Promise<AgentSession[]>;
  handleRenameAgent: (id: number, title: string) => Promise<void>;
  handleArchiveAgents: (ids: number[]) => Promise<void>;
  handleStopAgent: (id: number) => Promise<void>;
  handleRunAgent: (agent: AgentSession) => Promise<void>;
  handleDeleteAgent: (agent: AgentSession) => Promise<void>;
  openAgent: (id: number | null) => Promise<void>;
  selectAgentFromHome: (id: number | null) => void;
  syncFromRoute: (r: AppRoute) => void;
  enterOverview: () => void;
  startNew: () => void;
}

export function useAgentsController(deps: AgentsControllerDeps): AgentsController {
  const {
    sidebarMode,
    atHome,
    settingsReady,
    agentsEnabled,
    agentsAvailable,
    agentsRuntimeStarted,
    agentsUnavailableReason,
    notificationsEnabledRef,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeWsRoute,
    closeMobileNav,
    confirmAction,
    confirmLeaveRecording,
  } = deps;

  const [agentSettingsOpen, setAgentSettingsOpen] = useState(false);
  // Agents are loaded lazily when the user first enters agents mode.
  const [agents, setAgents] = useState<AgentSession[] | null>(null);
  const [showWorkflowAgents, setShowWorkflowAgents] = useState(true);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [startingAgentIds, setStartingAgentIds] = useState<Set<number>>(() => new Set());
  const [agentRunError, setAgentRunError] = useState<AgentRunError | null>(null);
  const [activeAgentId, setActiveAgentId] = useState<number | null>(
    // A legacy integer ref resolves immediately; a UUID waits for the list.
    () => resolveAgentRef(parseAppRoute().agentRef, null),
  );
  // A URL UUID we couldn't resolve yet (agent list not loaded). Resolved once
  // the sessions arrive. Initialised from the entry URL.
  const pendingAgentRef = useRef<string | null>(
    (() => {
      const ref = parseAppRoute().agentRef;
      return ref && !/^\d+$/.test(ref) ? ref : null;
    })(),
  );
  // Topic to preselect in the new-agent form, set when "/agent" (no prompt) is
  // run from a topic. Cleared once consumed.
  const [agentDraftTopicId, setAgentDraftTopicId] = useState<number | null>(null);
  // When nothing is selected, agents mode shows the fleet dashboard rather than
  // the start composer. This flag flips to the composer when the user hits
  // "New agent"; it resets to the dashboard whenever an agent is selected or we
  // leave agents mode.
  const [agentComposerOpen, setAgentComposerOpen] = useState(false);
  // Previous per-agent unread counts, so loadAgents can detect background
  // completions and fire a browser notification for newly-unread sessions.
  const agentUnreadRef = useRef<Map<number, number> | null>(null);
  // Previous per-agent status, so loadAgents can detect a transition INTO
  // needs_approval and fire the out-of-band "an agent is waiting for you"
  // signal (idea 5) that deep-links to the blocked agent.
  const agentStatusRef = useRef<Map<number, string> | null>(null);

  // Two async gaps sit between opening an agents surface and having something
  // to show: settings (which decide whether the feature is on at all) and the
  // session list. Both default to "off"/"empty", so rendering them straight
  // through flashes "Agents mode is off", then the start form, then the fleet.
  const agentsBooting = !settingsReady || (agentsEnabled && agents === null && !agentsError);

  // The currently-selected agent session, surfaced in the shared header.
  const activeAgent = useMemo(
    () => (agents ?? []).find((a) => a.id === activeAgentId) ?? null,
    [agents, activeAgentId],
  );
  const agentRunDisabledReason = !agentsEnabled
    ? "Agents mode is off"
    : !agentsAvailable
      ? agentsUnavailableReason || "The Copilot runtime is unavailable"
      : !agentsRuntimeStarted
        ? "The Copilot runtime did not start. Open Settings to recover it."
        : activeAgent && startingAgentIds.has(activeAgent.id)
          ? "Starting agent..."
          : activeAgent?.status === "interrupted"
            ? "Resume the interrupted turn from the timeline"
            : activeAgent && !agentCanStart(activeAgent)
              ? "Agent is already active"
              : null;

  // Mirror activeAgentId into a ref so changeMode can build the agents URL.
  const activeAgentIdRef = useRef<number | null>(activeAgentId);
  useEffect(() => {
    activeAgentIdRef.current = activeAgentId;
  }, [activeAgentId]);

  // Selecting an agent (or landing on one via a deep link) drops the transient
  // "start composer" state so returning to /agents shows the dashboard again.
  useEffect(() => {
    if (activeAgentId != null) setAgentComposerOpen(false);
  }, [activeAgentId]);

  // Leaving agents mode also resets the composer flag so the next visit to
  // /agents starts from the fleet dashboard, not a stale composer.
  useEffect(() => {
    if (sidebarMode !== "agents") setAgentComposerOpen(false);
  }, [sidebarMode]);

  // Mirror the loaded agent list into a ref so the mount-only URL sync handler
  // can resolve a UUID segment without re-subscribing.
  const agentsRef = useRef<AgentSession[] | null>(agents);
  useEffect(() => {
    agentsRef.current = agents;
  }, [agents]);

  // Once sessions load, resolve any UUID deep link that arrived before the list
  // was available (e.g. opening /agents/<uuid> cold).
  useEffect(() => {
    if (!pendingAgentRef.current || agents == null) return;
    const id = resolveAgentRef(pendingAgentRef.current, agents);
    if (id != null) {
      pendingAgentRef.current = null;
      setActiveAgentId(id);
    }
  }, [agents]);

  // Exclude the agent you're actively viewing (agents mode) from the tab total:
  // unlike topics/chats it isn't re-marked read on every incoming event, so its
  // backend count would otherwise keep the Agents tab badged while you watch it.
  const agentsUnread = useMemo(() => {
    const viewingId = sidebarMode === "agents" ? activeAgentId : null;
    return (agents ?? []).reduce(
      (n, a) => n + (a.id === viewingId ? 0 : (a.unread_count ?? 0)),
      0,
    );
  }, [agents, activeAgentId, sidebarMode]);
  // Agents blocked waiting for the human — surfaced as a bell in the tab title
  // so a background approval request is visible from any other tab.
  const agentsWaiting = useMemo(() => agentsWaitingCount(agents ?? []), [agents]);

  // activeAgentId -> /agents/<uuid> (or /agents when nothing is selected). The
  // canonical URL uses the public UUID; depends on `agents` so the link is
  // rewritten from a transient integer fallback once the list resolves.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "agents") return;
    // Don't clobber a deep-link URL whose agent we haven't resolved yet (the
    // list may still be loading). Overwriting it with "/agents" here would also
    // drop the UUID before the agents-load effect can resolve it.
    if (activeAgentId == null && pendingAgentRef.current) return;
    const target = agentUrl(activeAgentId, agents);
    if (window.location.pathname !== target) navigate(target);
  }, [activeAgentId, sidebarMode, agents, atHome]);

  useEffect(() => {
    function onOpenAgent(e: Event): void {
      const detail = (e as CustomEvent<{ id: number | null; topicId?: number }>).detail;
      const id = detail?.id ?? null;
      // A null id opens the new-agent form; carry the topic so it's preselected.
      setAgentDraftTopicId(id == null ? (detail?.topicId ?? null) : null);
      pendingAgentRef.current = null;
      setActiveAgentId(id);
      setAgentComposerOpen(id == null);
      setAtHome(false);
      closeMobileNav();
      closeWsRoute();
      setSidebarMode("agents");
    }
    window.addEventListener("precursor:open-agent", onOpenAgent);
    return () => window.removeEventListener("precursor:open-agent", onOpenAgent);
  }, []);

  // Live sync across windows: agent roster changes arrive over the shared event
  // bus. `start()` is idempotent, so App starting it too is harmless.
  useEffect(() => {
    eventBus.start();
    // A running agent emits SDK events at token cadence and each one lands here
    // as `agent.changed`. Coalesce the roster refresh so a burst costs one
    // `/api/agents` round-trip per window rather than one per event.
    const refreshAgents = coalesce(async () => {
      const list = await loadAgents();
      // Keep the session the user is actively viewing marked read as it
      // produces output, so its badge doesn't resurrect when they navigate
      // away (mirrors how a chat/topic is re-marked read on turn completion).
      // Gate on a settled status so we mark once per turn, not per streamed
      // event; markAgentRead doesn't publish, so this can't loop.
      const activeId = activeAgentIdRef.current;
      if (activeId == null || !isViewing("agent", activeId) || !windowFocused()) return;
      const active = list.find((a) => a.id === activeId);
      if (active && active.unread_count > 0 && AGENT_SETTLED_STATUSES.has(active.status)) {
        try {
          await api.agents.markRead(activeId);
          await loadAgents();
        } catch {
          // non-fatal
        }
      }
    });
    const off = eventBus.subscribe((event) => {
      if (event.type === "read.changed") {
        // Another tab marked a conversation read. A chat id wins over an agent
        // id, as in App's handler, which owns the other read.changed branches.
        if (event.chat_id == null && event.agent_session_id != null) refreshAgents();
      } else if (event.type === "agent.changed") {
        // An agent session was created, advanced, or finished (possibly in the
        // background). Refresh the list so statuses/badges stay current; the
        // AgentView refreshes its own timeline.
        refreshAgents();
      } else if (event.type === "workflow.changed") {
        // Workflow steps run as agent sessions, so the roster moves with the run.
        refreshAgents();
      }
    });
    return () => {
      off();
      refreshAgents.cancel();
    };
  }, []);

  async function handleRenameAgent(id: number, title: string): Promise<void> {
    await api.agents.rename(id, title);
    await loadAgents();
  }

  async function handleArchiveAgents(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.agents.archive(id)));
    if (activeAgentId != null && ids.includes(activeAgentId)) setActiveAgentId(null);
    await loadAgents();
  }

  async function handleStopAgent(id: number): Promise<void> {
    await api.agents.cancel(id);
    await loadAgents();
  }

  async function handleRunAgent(agent: AgentSession): Promise<void> {
    if (!agentsEnabled || !agentsAvailable || !agentCanStart(agent) || startingAgentIds.has(agent.id)) {
      return;
    }
    setStartingAgentIds((ids) => new Set(ids).add(agent.id));
    setAgentRunError(null);
    try {
      await api.agents.start(agent.id);
      await loadAgents();
    } catch (e) {
      setAgentRunError({ agentId: agent.id, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setStartingAgentIds((ids) => {
        const next = new Set(ids);
        next.delete(agent.id);
        return next;
      });
    }
  }

  async function handleDeleteAgent(agent: AgentSession): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete agent “${agent.title}”? Its session state is discarded.`,
        confirmLabel: "Delete",
        variant: "danger",
      }))
    )
      return;
    await api.agents.remove(agent.id);
    if (activeAgentId === agent.id) setActiveAgentId(null);
    await loadAgents();
  }

  async function openAgent(id: number | null): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    pendingAgentRef.current = null;
    setAtHome(false);
    closeWsRoute();
    setAgentComposerOpen(id == null);
    setActiveAgentId(id);
    setSidebarMode("agents");
    closeMobileNav();
  }

  // The "New agent" card's inline start form calls this once the agent exists.
  function selectAgentFromHome(id: number | null): void {
    if (id == null) return;
    setAtHome(false);
    setSidebarMode("agents");
    setActiveAgentId(id);
  }

  // The agents branch of App's mount + back/forward URL sync.
  function syncFromRoute(r: AppRoute): void {
    const id = resolveAgentRef(r.agentRef, agentsRef.current);
    setActiveAgentId(id);
    if (id != null) {
      pendingAgentRef.current = null;
    } else {
      // UUID not resolvable yet — stash it for the agents-load effect.
      pendingAgentRef.current = r.agentRef;
    }
  }

  // Section navigation always opens the Agents overview.
  function enterOverview(): void {
    pendingAgentRef.current = null;
    setActiveAgentId(null);
    setAgentComposerOpen(false);
  }

  // Drop the selection and reveal the "New agent" start composer.
  function startNew(): void {
    pendingAgentRef.current = null;
    setActiveAgentId(null);
    setAgentComposerOpen(true);
  }

  async function loadAgents(): Promise<AgentSession[]> {
    try {
      const list = await api.agents.list();
      // Notify for sessions whose unread grew since the last load — i.e. a
      // background/scheduled agent produced a new reply — skipping the very
      // first load and whichever session is currently open. Mirrors how a
      // finished topic turn notifies (see maybeNotify).
      const prev = agentUnreadRef.current;
      if (prev && notificationsEnabledRef.current) {
        for (const a of list) {
          const before = prev.get(a.id) ?? 0;
          if (a.unread_count > before && a.id !== activeAgentIdRef.current) {
            notifyIfUnfocused({
              title: a.title,
              body: "Agent has a new update.",
              tag: `precursor-agent-${a.id}`,
            });
          }
        }
      }
      // Out-of-band waiting signal: fire once when an agent transitions INTO
      // needs_approval, even if the user is looking at another part of the app,
      // so a blocked background agent never stalls unnoticed. Clicking jumps
      // straight to it.
      const prevStatus = agentStatusRef.current;
      if (prevStatus && notificationsEnabledRef.current) {
        for (const a of list) {
          const was = prevStatus.get(a.id);
          if (
            a.status === "needs_approval" &&
            was != null &&
            was !== "needs_approval" &&
            a.id !== activeAgentIdRef.current
          ) {
            const detail = a.pending_permission?.title;
            notifyNow({
              title: `🔔 ${a.title} needs approval`,
              body: detail ? `Waiting on: ${detail}` : "An agent is blocked waiting for you.",
              tag: `precursor-agent-approval-${a.id}`,
              requireInteraction: true,
              onClick: () => {
                setSidebarMode("agents");
                setActiveAgentId(a.id);
              },
            });
          }
        }
      }
      agentUnreadRef.current = new Map(list.map((a) => [a.id, a.unread_count ?? 0]));
      agentStatusRef.current = new Map(list.map((a) => [a.id, a.status]));
      setAgentsError(null);
      setAgents(list);
      return list;
    } catch (err) {
      setAgentsError(err instanceof Error ? err.message : "Could not load agents.");
      return agentsRef.current ?? [];
    }
  }

  // Mark the active agent read (and refresh badges) whenever it changes to a
  // real session — covers list clicks, the AgentView, deep links and route
  // sync in one place. markAgentRead doesn't publish, so this can't loop.
  useEffect(() => {
    if (activeAgentId == null) return;
    void api.agents.markRead(activeAgentId)
      .then(() => loadAgents())
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAgentId]);

  // Load agent sessions as soon as the feature is known-enabled (independent of
  // the current mode) so the mode-switcher badge and background completion
  // notifications work from anywhere, not just inside agents mode.
  useEffect(() => {
    if (!agentsEnabled || agents !== null) return;
    void loadAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentsEnabled]);

  return {
    agents,
    setAgents,
    showWorkflowAgents,
    setShowWorkflowAgents,
    agentsError,
    startingAgentIds,
    agentRunError,
    setAgentRunError,
    activeAgentId,
    setActiveAgentId,
    activeAgentIdRef,
    agentDraftTopicId,
    agentComposerOpen,
    setAgentComposerOpen,
    agentSettingsOpen,
    setAgentSettingsOpen,
    agentsEnabled,
    agentsAvailable,
    agentsRuntimeStarted,
    activeAgent,
    agentsBooting,
    agentRunDisabledReason,
    agentsUnread,
    agentsWaiting,
    loadAgents,
    handleRenameAgent,
    handleArchiveAgents,
    handleStopAgent,
    handleRunAgent,
    handleDeleteAgent,
    openAgent,
    selectAgentFromHome,
    syncFromRoute,
    enterOverview,
    startNew,
  };
}
