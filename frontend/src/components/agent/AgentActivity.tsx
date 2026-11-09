import { Fragment, useMemo } from "react";
import {
  buildRows,
  computeElapsedByEvent,
  computeModelByEvent,
  isNudgeRow,
  rowAlign,
  segmentRows,
  type WorkflowRow,
} from "../../lib/agentWorkflow";
import type { AgentEvent } from "../../lib/types";
import { ContinueMarker, HookGutter, StepConnector } from "../AgentTimeline";
import { MessageNode } from "./MessageNode";
import { ThinkingNode } from "./TimelineNodes";
import { ToolBox } from "./ToolBox";

function ActivityRow({
  row,
  model,
  autonomy,
  closed,
  modelByEvent,
  elapsedByEvent,
}: {
  row: WorkflowRow;
  model: string | null;
  autonomy: boolean;
  closed: boolean;
  modelByEvent: Map<AgentEvent, string>;
  elapsedByEvent: Map<AgentEvent, number>;
}) {
  if (row.type === "tool") {
    // Read-only: a historical attempt has no live session to decide a
    // permission against, so the box renders its detail only.
    return <ToolBox step={row.step} busy onDecision={() => {}} closed={closed} />;
  }
  if (row.type !== "node") return null;
  if (isNudgeRow(row)) return <ContinueMarker at={row.ev.at} />;
  if (row.cat === "reasoning") return <ThinkingNode reasoning={row.ev.text ?? ""} />;
  return (
    <MessageNode
      event={row.ev}
      category={row.cat}
      reasoning={row.reasoning}
      isLastAnswer={false}
      autonomy={autonomy}
      model={modelByEvent.get(row.ev) ?? model}
      elapsedMs={elapsedByEvent.get(row.ev) ?? null}
      suggestionsDisabled
    />
  );
}

/**
 * The agent workflow timeline, read-only.
 *
 * The same rows the Agents cockpit renders — tool boxes with their arguments and
 * output, reasoning, assistant messages, lifecycle hooks — built by the same
 * ``buildRows``, so a workflow step's activity is described exactly as a
 * standalone agent's is rather than by a lookalike that drifts from it. What's
 * dropped is only what needs a live session: approving a parked permission,
 * suggestion chips, and the reply affordance.
 */
export function AgentActivity({
  events,
  model = null,
  autonomy = false,
  closed = false,
}: {
  events: AgentEvent[];
  /** Session model, used when a turn reported no usage event of its own. */
  model?: string | null;
  /** Gates directive parsing, matching the cockpit's behaviour. */
  autonomy?: boolean;
  /**
   * This stream has ended — a finished workflow step attempt. Tool calls left
   * unterminated by it are reported as interrupted (or never approved) rather
   * than spinning as though they were still going.
   */
  closed?: boolean;
}) {
  const elapsedByEvent = useMemo(() => computeElapsedByEvent(events), [events]);
  const modelByEvent = useMemo(() => computeModelByEvent(events), [events]);
  // Segment exactly as the cockpit does.
  const { segments, trailingHooks } = useMemo(() => segmentRows(buildRows(events)), [events]);

  if (segments.length === 0 && trailingHooks.length === 0) {
    return <p className="py-2 text-center text-[11px] text-muted">No activity was recorded for this attempt.</p>;
  }

  return (
    // Same width the cockpit gives its timeline. The spine is centred on this
    // column, so letting it stretch to the full trace width would fling the
    // left- and right-aligned bubbles away from the arrows that connect them.
    <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col items-center">
      {segments.map((seg, idx) => (
        <Fragment key={idx}>
          {idx === 0 ? seg.hooks.length > 0 && <HookGutter hooks={seg.hooks} /> : <StepConnector hooks={seg.hooks} />}
          <div className={`flex w-full ${rowAlign(seg.row, { centerNudge: false })}`}>
            <ActivityRow
              row={seg.row}
              model={model}
              autonomy={autonomy}
              closed={closed}
              modelByEvent={modelByEvent}
              elapsedByEvent={elapsedByEvent}
            />
          </div>
        </Fragment>
      ))}
      {trailingHooks.length > 0 && <HookGutter hooks={trailingHooks} />}
    </div>
  );
}
