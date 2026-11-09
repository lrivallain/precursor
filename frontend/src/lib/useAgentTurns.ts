import { useCallback, useMemo } from "react";
import type { AgentPane } from "../components/AgentResults";
import { buildTurnGroup, withRewindableIds, type TurnGroup } from "./agentCockpitModel";
import type { CockpitTimeline } from "./agentCockpitTimeline";
import { api } from "./api";
import { useAgentRewind } from "./useAgentRewind";
import { useMessageJump } from "./useMessageLink";
import type { RewindController } from "./useRewind";
import { useTimelineView, type TurnTimeline } from "./useTurnTimeline";
import type { TimelineScroll } from "./useAgentTimelineScroll";
import type { AgentRun } from "./types";

export interface PromptRewindActions {
  onRewindHere?: () => void;
  onEditResend?: () => void;
}

export interface AgentTurnsOptions {
  agentId: number;
  runFilter: number | null;
  pane: AgentPane;
  timeline: CockpitTimeline;
  runs: AgentRun[];
  rewindableRunIds: readonly number[];
  turnActive: boolean;
  scroll: TimelineScroll;
  /** The open message permalink's target, if any. */
  eventTarget: string | null;
  /** The transcript on screen has been read. */
  eventsReady: boolean;
  setDraft: (text: string) => void;
  /** After a rewind lands: re-read the transcript and the session list. */
  onRewound: () => void;
  onError: (message: string) => void;
}

export interface AgentTurns {
  turnTimeline: TurnTimeline;
  rewind: RewindController;
  /** Set when a message permalink couldn't be shown. */
  linkError: string | null;
  turnGroup: TurnGroup | undefined;
  /** Rewind / Edit & resend on the prompt of an exchange, when offered. */
  promptRewindActions: (exchange: number) => PromptRewindActions;
}

/**
 * The timeline rail and rewind. Turns come from the transcript itself (one per
 * human prompt); only the current SDK session's prompts can be rewound.
 */
export function useAgentTurns(opts: AgentTurnsOptions): AgentTurns {
  const { agentId, runFilter, timeline, runs, rewindableRunIds, turnActive, scroll } = opts;
  const agentTurns = useMemo(
    () => withRewindableIds(timeline.turns, rewindableRunIds),
    [timeline.turns, rewindableRunIds],
  );
  const turnGroup = useMemo(
    () => buildTurnGroup(agentTurns, runs, rewindableRunIds),
    [agentTurns, runs, rewindableRunIds],
  );
  const scope = `${agentId}:${runFilter ?? "all"}`;
  const turnTimeline = useTimelineView({
    // The scroll container mounts with the Activity pane; the listener must
    // re-attach when it does.
    resetKey: `${scope}:${opts.pane}`,
    turns: agentTurns,
    scrollRef: scroll.scrollRef,
    content: timeline,
    ensureLoaded: scroll.ensureTurnMounted,
  });
  const linkError = useMessageJump({
    target: opts.eventTarget,
    ready: opts.eventsReady,
    resetKey: String(agentId),
    scrollRef: scroll.scrollRef,
    unpin: scroll.unpin,
    prepare: async (target) => {
      const at = timeline.segments.findIndex(
        ({ entry }) => entry.kind === "row" && entry.row.type === "node" && entry.row.ev.link_id === target,
      );
      if (at < 0) return false;
      scroll.revealFrom(at);
      return true;
    },
  });
  const rewind = useAgentRewind({
    resetKey: scope,
    turns: agentTurns,
    streaming: turnActive,
    rewind: (eventId, mode) => api.agents.rewind(agentId, eventId, mode),
    previewRewind: (eventId) => api.agents.previewRewind(agentId, eventId),
    setDraft: opts.setDraft,
    onSettled: opts.onRewound,
    onError: opts.onError,
  });
  const turnIndexByExchange = useMemo(
    () => new Map(agentTurns.map((t, i) => [t.message_id, i])),
    [agentTurns],
  );
  const { canRewind, start: startRewind, busy: rewindBusy } = rewind;
  const turns = turnTimeline.turns;
  const promptRewindActions = useCallback(
    (exchange: number): PromptRewindActions => {
      const index = turnIndexByExchange.get(exchange);
      if (index === undefined || turnActive || rewindBusy) return {};
      const offer = (mode: "rewind" | "edit") =>
        canRewind?.(index, mode) ? () => startRewind(turns, index, mode) : undefined;
      return { onRewindHere: offer("rewind"), onEditResend: offer("edit") };
    },
    [turnIndexByExchange, turnActive, rewindBusy, canRewind, startRewind, turns],
  );
  return { turnTimeline, rewind, linkError, turnGroup, promptRewindActions };
}
