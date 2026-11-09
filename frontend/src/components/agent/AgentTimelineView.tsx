import { useCallback, type ReactNode } from "react";
import type { CockpitTimeline, RowEntry, TimelineSegment } from "../../lib/agentCockpitTimeline";
import { findPublished, type ResultVersion } from "../../lib/agentResults";
import { isNudgeRow, rowAlign, type NodeRow } from "../../lib/agentWorkflow";
import { parseAgentDirectives, type AgentArtifactDirective } from "../../lib/directives";
import { discussionPermalink, messagePermalink } from "../../lib/routes";
import type { PromptRewindActions } from "../../lib/useAgentTurns";
import { TURN_ANCHOR_ATTR } from "../../lib/useTurnTimeline";
import type { AgentEvent, AgentLiveThinking, AgentSession } from "../../lib/types";
import { InlineArtifact, ResultCard } from "../AgentResults";
import { ContinueMarker, HookGutter, StepConnector, WorkFold } from "../AgentTimeline";
import { RewindCutLine } from "../ConversationTranscript";
import { MessageNode } from "./MessageNode";
import { MissionMilestone, PendingUserBubble, ThinkingNode, type Persona } from "./TimelineNodes";
import { ToolBox, type DecisionHandler } from "./ToolBox";

export type ArtifactRenderer = (at: string | null) => (a: AgentArtifactDirective, i: number) => ReactNode;

/**
 * How an answer shows what it published: a card for its result version, or
 * (not on the blackboard) the payload folded inline.
 */
export function usePublishedRenderer(
  versions: ResultVersion[],
  latestN: number,
  openVersion: (v: ResultVersion) => void,
): ArtifactRenderer {
  return useCallback(
    (at: string | null) => (a: AgentArtifactDirective, i: number) => {
      const hit = findPublished(versions, a.title, at);
      if (!hit) return <InlineArtifact key={i} artifact={a} />;
      return (
        <ResultCard
          key={i}
          artifact={hit.artifact}
          version={hit.version}
          latest={latestN}
          onOpen={() => openVersion(hit.version)}
        />
      );
    },
    [versions, latestN, openVersion],
  );
}

/** What a live row of the cockpit timeline needs from the view. */
export interface RowContext {
  agent: AgentSession;
  user: Persona;
  showThinking: boolean;
  /** Approval buttons are locked (busy, or the runtime isn't ready). */
  toolBusy: boolean;
  onDecision: DecisionHandler;
  modelByEvent: Map<AgentEvent, string>;
  elapsedByEvent: Map<AgentEvent, number>;
  latestAnswerKey: string | null;
  onPickSuggestion: (text: string) => void;
  onReply: () => void;
  suggestionsDisabled: boolean;
  renderArtifact: ArtifactRenderer;
  /** The exchange the timeline rail marks as current. */
  currentExchange: number | null;
  /** Show the current prompt's toolbar without hovering (reading back). */
  revealCurrent: boolean;
  rewindActions: (exchange: number) => PromptRewindActions;
  onToggleFold: (exchange: number) => void;
}

function CockpitMessage({ entry, row, ctx, current }: { entry: RowEntry; row: NodeRow; ctx: RowContext; current: boolean }) {
  const { agent } = ctx;
  const opensTurn = entry.role === "prompt";
  return (
    <MessageNode
      event={row.ev}
      permalink={
        agent.public_id && row.ev.link_id
          ? messagePermalink(discussionPermalink("agent", agent.public_id), row.ev.link_id, "event")
          : undefined
      }
      category={row.cat}
      reasoning={ctx.showThinking ? row.reasoning : null}
      isLastAnswer={entry.role === "answer"}
      autonomy={agent.autonomy_enabled}
      user={ctx.user}
      model={ctx.modelByEvent.get(row.ev) ?? agent.model ?? null}
      elapsedMs={ctx.elapsedByEvent.get(row.ev) ?? null}
      // Suggested replies follow up on where things stand now, so only the
      // newest answer offers them.
      onPickSuggestion={entry.key === ctx.latestAnswerKey ? ctx.onPickSuggestion : undefined}
      onReply={ctx.onReply}
      suggestionsDisabled={ctx.suggestionsDisabled}
      renderArtifact={ctx.renderArtifact(row.ev.at)}
      highlighted={current}
      revealActions={current && ctx.revealCurrent}
      {...(opensTurn ? ctx.rewindActions(entry.exchange) : {})}
    />
  );
}

function RowContent({ entry, ctx, current }: { entry: RowEntry; ctx: RowContext; current: boolean }) {
  const { row } = entry;
  if (row.type === "tool") return <ToolBox step={row.step} busy={ctx.toolBusy} onDecision={ctx.onDecision} />;
  if (row.type !== "node") return null;
  if (isNudgeRow(row)) return <ContinueMarker at={row.ev.at} />;
  if (row.cat === "reasoning") return <ThinkingNode reasoning={row.ev.text ?? ""} />;
  return <CockpitMessage entry={entry} row={row} ctx={ctx} current={current} />;
}

// Progress heartbeats stay on the spine. The terminal OBJECTIVE_COMPLETE is
// folded into the answer bubble itself, so it isn't repeated here.
function AssistantMilestone({ row, autonomy }: { row: NodeRow; autonomy: boolean }) {
  if (row.cat !== "assistant" || !autonomy) return null;
  const d = parseAgentDirectives(row.ev.text ?? "");
  if (d.complete != null || !d.progress) return null;
  return <MissionMilestone progress={d.progress} at={row.ev.at} />;
}

function SegmentView({
  seg,
  first,
  rewindFrom,
  ctx,
}: {
  seg: TimelineSegment;
  first: boolean;
  rewindFrom: number | null;
  ctx: RowContext;
}) {
  const { entry } = seg;
  const connector = first ? seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} /> : <StepConnector hooks={seg.hooks} />;
  // A previewed rewind dims the exchanges it would drop.
  const doomed = rewindFrom != null && entry.exchange >= rewindFrom;
  const dim = `flex w-full flex-col items-center${doomed ? " opacity-35 grayscale transition-opacity" : ""}`;
  if (entry.kind === "fold") {
    return (
      <div className={dim}>
        {connector}
        <WorkFold stats={entry.stats} open={entry.open} onToggle={() => ctx.onToggleFold(entry.exchange)} />
      </div>
    );
  }
  const { row } = entry;
  const opensTurn = entry.role === "prompt";
  const current = opensTurn && ctx.currentExchange != null && entry.exchange === ctx.currentExchange;
  const link = row.type === "node" ? row.ev.link_id : undefined;
  return (
    <div className={dim}>
      {opensTurn && entry.exchange === rewindFrom && (
        <div className="mt-2 w-full max-w-xl">
          <RewindCutLine />
        </div>
      )}
      {connector}
      <div
        className={`flex w-full scroll-mt-3 ${rowAlign(row, { centerNudge: true })}`}
        data-exchange={opensTurn ? entry.exchange : undefined}
        data-message-link={link}
        tabIndex={link ? -1 : undefined}
        {...(opensTurn ? { [TURN_ANCHOR_ATTR]: entry.exchange } : {})}
      >
        <RowContent entry={entry} ctx={ctx} current={current} />
      </div>
      {row.type === "node" && <AssistantMilestone row={row} autonomy={ctx.agent.autonomy_enabled} />}
    </div>
  );
}

function Pending({ text, user }: { text: string; user: Persona }) {
  return (
    <div className="flex w-full justify-end">
      <PendingUserBubble text={text} user={user} />
    </div>
  );
}

/**
 * The cockpit's workflow timeline. Only segments from `windowStart` onward are
 * mounted: it's an absolute index and keys come from the rows themselves, so
 * appended steps never shift on-screen history; scrolling up lowers it to
 * reveal older boxes.
 */
export function AgentTimelineView({
  timeline,
  windowStart,
  rewindFrom,
  pendingText,
  liveThinking,
  ctx,
}: {
  timeline: CockpitTimeline;
  windowStart: number;
  /** First exchange a previewed rewind would drop. */
  rewindFrom: number | null;
  /** The optimistic echo of a just-sent prompt, while it's on screen. */
  pendingText: string | null;
  liveThinking: AgentLiveThinking | null;
  ctx: RowContext;
}) {
  const { segments, trailingHooks } = timeline;
  if (segments.length === 0 && trailingHooks.length === 0 && !liveThinking) {
    if (pendingText == null) return <p className="text-[11px] text-muted">No steps recorded yet.</p>;
    return (
      <div className="flex flex-col items-center">
        <Pending text={pendingText} user={ctx.user} />
      </div>
    );
  }
  const hiddenCount = Math.min(windowStart, segments.length);
  const shown = hiddenCount > 0 ? segments.slice(hiddenCount) : segments;
  return (
    <div className="flex flex-col items-center">
      {hiddenCount > 0 && <p className="mb-2 text-[11px] text-muted">Scroll up to load earlier steps…</p>}
      {shown.map((seg, idx) => (
        <SegmentView key={seg.entry.key} seg={seg} first={hiddenCount + idx === 0} rewindFrom={rewindFrom} ctx={ctx} />
      ))}
      {trailingHooks.length > 0 && (
        <div className={`flex w-full flex-col items-center${rewindFrom != null ? " opacity-35 grayscale" : ""}`}>
          <HookGutter hooks={trailingHooks} />
        </div>
      )}
      {liveThinking && ctx.showThinking && (
        <>
          <StepConnector hooks={[]} />
          <ThinkingNode reasoning={liveThinking.text} live={liveThinking.active} />
        </>
      )}
      {pendingText != null && (
        <>
          <StepConnector hooks={[]} />
          <Pending text={pendingText} user={ctx.user} />
        </>
      )}
    </div>
  );
}
