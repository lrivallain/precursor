import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { agentStatusFlags, userPromptHistory } from "../../lib/agentCockpitModel";
import { buildCockpitTimeline } from "../../lib/agentCockpitTimeline";
import { useShowPrefs } from "../../lib/agentShowPrefs";
import { computeElapsedByEvent, computeModelByEvent } from "../../lib/agentWorkflow";
import { useAgentCockpitActions } from "../../lib/useAgentCockpitActions";
import { useAgentEvents } from "../../lib/useAgentEvents";
import { usePendingEcho, type PendingEcho } from "../../lib/useAgentPendingEcho";
import { useAgentResults } from "../../lib/useAgentResults";
import { useAgentRuns } from "../../lib/useAgentRuns";
import type { AgentComposerInfra } from "../../lib/useAgentStartForm";
import { useAgentTimelineScroll } from "../../lib/useAgentTimelineScroll";
import { useAgentTurns } from "../../lib/useAgentTurns";
import type { BusyAction } from "../../lib/useBusyAction";
import { useDiscussionTarget } from "../../lib/useMessageLink";
import type { AgentSession } from "../../lib/types";
import { AgentPaneTabs, AgentResultView } from "../AgentResults";
import { TimelineRail } from "../TimelineRail";
import { AgentAttention, MissionStrip, RunFilterNotice } from "./AgentAttention";
import { AgentFollowUpComposer } from "./AgentFollowUpComposer";
import { AgentRuntimeStrip } from "./AgentGates";
import { AgentInsightsPanel } from "./AgentInsightsPanel";
import { AgentTimelineView, usePublishedRenderer, type RowContext } from "./AgentTimelineView";
import { ArtifactViewer } from "./ArtifactViewer";
import type { Persona } from "./TimelineNodes";

export interface AgentCockpitProps {
  agent: AgentSession;
  enabled: boolean;
  needsRuntime: boolean;
  runtimeReady: boolean;
  runtimeCard: ReactNode;
  onOpenSettings: () => void;
  onReload: () => void;
  onSetRole?: (roleName: string | null) => Promise<void>;
  /** Shared with the start form: a create's echo and error carry over. */
  action: BusyAction;
  pending: PendingEcho | null;
  setPending: Dispatch<SetStateAction<PendingEcho | null>>;
  followUp: string;
  setFollowUp: Dispatch<SetStateAction<string>>;
  composer: AgentComposerInfra;
  roleOpen: boolean;
  setRoleOpen: (open: boolean) => void;
  user: Persona;
}

// The transcript as exchanges, with the turns the user unfolded. Those are by
// exchange index, so they're scoped to the agent and run on screen.
function useCockpitTimeline(p: AgentCockpitProps, runFilter: number | null, eventTarget: string | null) {
  const { agent } = p;
  const { events, ...feed } = useAgentEvents(agent.id, runFilter, p.action.setError);
  const showPending = usePendingEcho(events, agent.id, p.pending, p.setPending);
  const [showPrefs, toggleShow] = useShowPrefs();
  const flags = agentStatusFlags(agent.status);
  const [expanded, setExpanded] = useState<ReadonlySet<number>>(() => new Set());
  useEffect(() => {
    setExpanded(new Set());
  }, [agent.id, runFilter]);
  const timeline = useMemo(
    () =>
      buildCockpitTimeline(events, {
        showPrefs,
        terminal: flags.terminal,
        live: flags.turnActive,
        autonomy: agent.autonomy_enabled ?? false,
        expanded,
        targetId: eventTarget,
      }),
    [events, showPrefs, flags.terminal, flags.turnActive, agent.autonomy_enabled, expanded, eventTarget],
  );
  const toggleExpanded = useCallback(
    (exchange: number, forceClose: boolean) =>
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(exchange) || forceClose) next.delete(exchange);
        else next.add(exchange);
        return next;
      }),
    [],
  );
  const elapsedByEvent = useMemo(() => computeElapsedByEvent(events), [events]);
  const modelByEvent = useMemo(() => computeModelByEvent(events), [events]);
  const userHistory = useMemo(() => userPromptHistory(events), [events]);
  return { events, ...feed, showPending, showPrefs, toggleShow, flags, timeline, toggleExpanded, elapsedByEvent, modelByEvent, userHistory };
}

function useCockpitState(p: AgentCockpitProps) {
  const { agent } = p;
  const eventTarget = useDiscussionTarget("agent", agent.public_id);
  const runs = useAgentRuns(agent.id, Boolean(eventTarget));
  const tl = useCockpitTimeline(p, runs.runFilter, eventTarget);
  const results = useAgentResults({
    agentId: agent.id,
    runFilter: runs.runFilter,
    runsLoaded: runs.runsLoaded,
    anchors: tl.timeline.anchors,
    turnActive: tl.flags.turnActive,
    atRest: tl.flags.atRest,
    needsYou: tl.flags.needsYou,
    showPending: tl.showPending,
    linked: Boolean(eventTarget),
    leaveEventLink: runs.leaveEventLink,
  });
  const { setPaneChoice } = results;
  const showActivity = useCallback(() => setPaneChoice("activity"), [setPaneChoice]);
  const scroll = useAgentTimelineScroll({
    agentId: agent.id,
    content: tl.events,
    showPending: tl.showPending,
    pane: results.pane,
    segmentCount: tl.timeline.segments.length,
    exchangeStart: tl.timeline.exchangeStart,
    onShowActivity: showActivity,
    shownVersion: results.shownVersion?.n ?? null,
  });
  const { reloadFresh } = tl;
  const { onReload, setFollowUp } = p;
  const onRewound = useCallback(() => {
    reloadFresh();
    onReload();
  }, [reloadFresh, onReload]);
  const turns = useAgentTurns({
    agentId: agent.id,
    runFilter: runs.runFilter,
    pane: results.pane,
    timeline: tl.timeline,
    runs: runs.runs,
    rewindableRunIds: tl.rewindableRunIds,
    turnActive: tl.flags.turnActive,
    scroll,
    eventTarget,
    eventsReady: tl.ready,
    setDraft: setFollowUp,
    onRewound,
    onError: p.action.setError,
  });
  return { eventTarget, runs, tl, results, scroll, turns };
}

type CockpitState = ReturnType<typeof useCockpitState>;

// The open agent's controls, and what a timeline row needs from them.
function useCockpitControls(p: AgentCockpitProps, { eventTarget, runs, tl, results, scroll, turns }: CockpitState) {
  const { agent, action } = p;
  const actions = useAgentCockpitActions({
    agentId: agent.id,
    action,
    rewindBusy: Boolean(turns.rewind.busy),
    followUp: p.followUp,
    setFollowUp: p.setFollowUp,
    setPending: p.setPending,
    openRolePicker: () => p.setRoleOpen(true),
    followTurn: results.followTurn,
    onReload: p.onReload,
  });
  // Wrapper around the reply composer, so a raised NEED_INPUT question can jump
  // the human straight to the answer box (scroll into view + focus).
  const composerWrapRef = useRef<HTMLDivElement>(null);
  const focusComposer = useCallback(() => {
    const wrap = composerWrapRef.current;
    if (!wrap) return;
    wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    wrap.querySelector("textarea")?.focus();
  }, []);
  const renderArtifact = usePublishedRenderer(results.versions, results.latestVersion?.n ?? 0, results.openVersion);
  const toggleFold = (exchange: number) => {
    // Opening a fold near the bottom must not yank the view down to the end.
    scroll.unpin();
    const linkedFold =
      Boolean(eventTarget) &&
      tl.timeline.segments.some(
        ({ entry }) =>
          entry.exchange === exchange && entry.kind === "row" && entry.row.type === "node" && entry.row.ev.link_id === eventTarget,
      );
    runs.leaveEventLink();
    tl.toggleExpanded(exchange, linkedFold);
  };
  const actionsDisabled = action.busy || !p.runtimeReady;
  const rowCtx: RowContext = {
    agent,
    user: p.user,
    showThinking: tl.showPrefs.thinking,
    toolBusy: actionsDisabled,
    onDecision: actions.approve,
    modelByEvent: tl.modelByEvent,
    elapsedByEvent: tl.elapsedByEvent,
    latestAnswerKey: tl.timeline.latestAnswerKey,
    onPickSuggestion: (text) => void actions.sendFollowUp(text),
    onReply: focusComposer,
    suggestionsDisabled: !p.runtimeReady || tl.flags.turnActive,
    renderArtifact,
    currentExchange: turns.turnTimeline.currentMessageId,
    revealCurrent: !turns.turnTimeline.view.atLatest && !turns.rewind.preview,
    rewindActions: turns.promptRewindActions,
    onToggleFold: toggleFold,
  };
  const attention = (
    <AgentAttention
      agent={agent}
      pane={results.pane}
      actionsDisabled={actionsDisabled}
      onReview={() => results.setPaneChoice("activity")}
      onAnswer={focusComposer}
      onStartNow={actions.startNow}
      onResume={actions.resume}
    />
  );
  return { actions, composerWrapRef, rowCtx, attention };
}

function ActivityPane({
  state: { runs, tl, results, scroll, turns },
  p,
  rowCtx,
  errorLine,
  attention,
}: {
  state: CockpitState;
  p: AgentCockpitProps;
  rowCtx: RowContext;
  errorLine: ReactNode;
  /** What needs you, above the timeline. */
  attention: ReactNode;
}) {
  const tabbed = results.latestVersion != null;
  return (
    // Scrollable workflow region, with the turn timeline rail beside it.
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scroll.scrollRef}
        onScroll={scroll.onScroll}
        role={tabbed ? "tabpanel" : undefined}
        id={tabbed ? "agent-panel-activity" : undefined}
        aria-labelledby={tabbed ? "agent-pane-activity" : undefined}
        className="flex-1 overflow-y-auto py-3 pl-5 pr-12"
      >
        <div ref={scroll.innerRef}>
          {errorLine}
          {turns.linkError && <p role="status" className="mb-2 text-sm text-red-500">{turns.linkError}</p>}
          <RunFilterNotice
            runFilter={runs.runFilter}
            following={runs.runChoice == null}
            runCount={runs.runs.length}
            onShowAll={() => runs.setRunChoice("all")}
          />
          <MissionStrip agent={p.agent} />
          {attention}
          <AgentTimelineView
            timeline={tl.timeline}
            windowStart={scroll.windowStart}
            rewindFrom={turns.rewind.preview?.fromId ?? null}
            pendingText={tl.showPending && p.pending ? p.pending.text : null}
            liveThinking={tl.liveThinking}
            ctx={rowCtx}
          />
        </div>
      </div>
      <TimelineRail timeline={turns.turnTimeline} rewind={turns.rewind} streaming={tl.flags.turnActive} group={turns.turnGroup} />
    </div>
  );
}

function ResultPane({
  state: { results, scroll },
  agent,
  errorLine,
  attention,
}: {
  state: CockpitState;
  agent: AgentSession;
  errorLine: ReactNode;
  attention: ReactNode;
}) {
  if (!results.shownVersion) return null;
  return (
    <div
      ref={scroll.resultScrollRef}
      role="tabpanel"
      id="agent-panel-result"
      aria-labelledby="agent-pane-result"
      className="flex-1 overflow-y-auto"
    >
      <div className="px-5 pt-3 empty:hidden">
        {errorLine}
        {attention}
      </div>
      <AgentResultView
        agent={agent}
        versions={results.versions}
        shown={results.shownVersion}
        onSelect={results.openVersion}
        onShowInActivity={scroll.showInActivity}
      />
    </div>
  );
}

/** A selected agent: its result or activity, the follow-up box and the insights rail. */
export function AgentCockpit(p: AgentCockpitProps) {
  const { agent, action, pending } = p;
  const state = useCockpitState(p);
  const { runs, tl, results, scroll, turns } = state;
  const { actions, composerWrapRef, rowCtx, attention } = useCockpitControls(p, state);
  const { pane, latestVersion } = results;
  const showResult = pane === "result" && results.shownVersion != null;
  const errorLine = action.error && <p className="mb-2 text-[11px] text-red-500">{action.error}</p>;

  return (
    <div className="flex h-full min-h-0 w-full">
      <div className="mx-auto flex h-full min-w-0 w-full max-w-3xl flex-col">
        <AgentRuntimeStrip
          needsRuntime={p.needsRuntime}
          enabled={p.enabled}
          runtimeCard={p.runtimeCard}
          onOpenSettings={p.onOpenSettings}
        />
        {latestVersion && (
          <AgentPaneTabs
            pane={pane}
            onChange={(next) => {
              runs.leaveEventLink();
              results.setPaneChoice(next);
            }}
            latest={latestVersion.n}
            live={tl.flags.turnActive}
          />
        )}
        {showResult ? (
          <ResultPane state={state} agent={agent} errorLine={errorLine} attention={attention} />
        ) : (
          <ActivityPane state={state} p={p} rowCtx={rowCtx} errorLine={errorLine} attention={attention} />
        )}
        <AgentFollowUpComposer
          wrapRef={composerWrapRef}
          pane={pane}
          turnTimeline={turns.turnTimeline}
          rewind={turns.rewind}
          scrollRef={scroll.scrollRef}
          turnActive={tl.flags.turnActive}
          sending={action.busy && pending != null}
          runtimeReady={p.runtimeReady}
          value={p.followUp}
          onChange={p.setFollowUp}
          onSend={() => void actions.sendFollowUp()}
          onStop={actions.stop}
          userHistory={tl.userHistory}
          composer={p.composer}
          role={agent.role ?? null}
          onSetRole={(roleName) => void p.onSetRole?.(roleName)}
          roleOpen={p.roleOpen}
          onRoleOpenChange={p.setRoleOpen}
        />
      </div>
      <AgentInsightsPanel
        showPrefs={tl.showPrefs}
        toggleShow={tl.toggleShow}
        events={tl.events}
        model={agent.model ?? null}
        agent={agent}
        runs={runs.runs}
        runFilter={runs.runFilter}
        onRunFilterChange={(next) => {
          runs.leaveEventLink();
          runs.setRunChoice(next);
        }}
        artifacts={results.artifacts}
        artifactLabel={results.artifactLabel}
        onOpenArtifact={results.openArtifact}
        compact={{
          busy: action.busy && pending?.text.startsWith("/compact") === true,
          disabled: action.busy || agent.status === "running" || agent.status === "needs_approval",
          disabledReason: "The agent is busy",
          run: () => void actions.sendFollowUp("/compact"),
        }}
      />
      {results.viewing && <ArtifactViewer agent={agent} artifact={results.viewing} onClose={results.closeViewer} />}
    </div>
  );
}
