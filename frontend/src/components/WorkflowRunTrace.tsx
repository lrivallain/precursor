import {
  ArrowDownToLine,
  ArrowUpToLine,
  Activity,
  Coins,
  Loader2,
  RotateCw,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import type { AgentEvent, WorkflowRun, WorkflowRunStep } from "../lib/types";
import { AgentActivity } from "./AgentView";
import { CopyableMarkdown } from "./CopyableMarkdown";
import {
  formatTokens,
  gateVerdictChip,
  stepElapsed,
  traceMeta,
  workflowRelativeTime,
} from "../lib/workflows";

interface Props {
  run: WorkflowRun;
  /** The workflow the run belongs to — needed to fetch per-attempt activity. */
  workflowId: number;
  /**
   * Re-run one recorded attempt on its own, with the input it first saw.
   * Omitted while a run is in flight, which is when a replay would race the
   * coordinator for the step's agent.
   */
  onReplay?: (stepRunId: number) => Promise<void>;
  /** Deep-link into the full Agents cockpit for a trace's agent. */
  onOpenAgent?: (agentId: number) => void;
}

/**
 * What the agent actually *did* during one step attempt.
 *
 * The surrounding row says what a step received and produced; when a step blocks
 * or stalls having produced nothing, that leaves nothing to diagnose from. This
 * renders the same timeline the Agents cockpit does — tool calls with their
 * arguments and output, reasoning, errors — fetched lazily, because a run has
 * many attempts and only the interesting one gets opened.
 */
function StepActivity({ workflowId, step }: { workflowId: number; step: WorkflowRunStep }) {
  const [events, setEvents] = useState<AgentEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    if (events !== null || loading) return;
    setLoading(true);
    setError(null);
    try {
      setEvents(await api.workflows.stepEvents(workflowId, step.id));
    } catch {
      setError("Couldn't load this attempt's activity.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <details
      className="group mt-1.5"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) void load();
      }}
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] font-medium text-muted transition hover:text-fg">
        <Activity size={12} className="text-violet-500" />
        Activity
        {loading && <Loader2 size={10} className="animate-spin" />}
      </summary>
      <div className="mt-1.5 max-h-[28rem] overflow-y-auto rounded-lg border border-border bg-bg/40 px-2.5 py-2">
        {error ? (
          <p className="py-2 text-center text-[11px] text-red-500">{error}</p>
        ) : events === null ? (
          <p className="py-2 text-center text-[11px] text-muted">Loading…</p>
        ) : (
          // A finished attempt's stream is closed: whatever it left open was
          // abandoned when the attempt ended, not still going.
          <AgentActivity events={events} closed={step.finished_at != null} />
        )}
      </div>
    </details>
  );
}

/**
 * Vertical, append-only trace of one run's step attempts. Each row shows what a
 * step *received* (input context) and *produced* (output), plus gate verdicts
 * and per-attempt duration — the durable, inspectable record the run picker
 * scrolls through. A gate loop-back appears as a fresh row with a bumped
 * attempt badge, so retries read as distinct entries rather than mutations.
 */
export function WorkflowRunTrace({ run, workflowId, onReplay, onOpenAgent }: Props) {
  // Which attempt's replay is in flight, so only its own icon spins.
  const [replayingId, setReplayingId] = useState<number | null>(null);

  if (run.step_runs.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-xs text-muted">
        This run has no recorded steps yet.
      </p>
    );
  }

  async function replay(stepRunId: number): Promise<void> {
    if (!onReplay || replayingId != null) return;
    setReplayingId(stepRunId);
    try {
      await onReplay(stepRunId);
    } finally {
      setReplayingId(null);
    }
  }

  return (
    <ol className="relative space-y-3 pl-10">
      {/* Rail — 2px line centered on the 18px node dots (node center ~19px). */}
      <span className="absolute bottom-2 left-[18px] top-2 w-0.5 rounded bg-border" aria-hidden />
      {run.step_runs.map((s) => {
        const meta = traceMeta(s.status);
        const isGate = s.kind === "gate";
        const isApproval = s.kind === "approval";
        const running = s.status === "running";
        const duration = stepElapsed(s);
        const tokens = (s.input_tokens || 0) + (s.output_tokens || 0);
        return (
          <li key={s.id} className="relative">
            {/* Rail node */}
            <span
              className={`absolute -left-[30px] top-2 flex h-[18px] w-[18px] items-center justify-center rounded-full border-2 border-bg ${meta.dot}`}
              aria-hidden
            >
              {running && <Loader2 size={10} className="animate-spin text-white" />}
            </span>

            <div className="rounded-xl border border-border bg-surface/60 px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
                  {isGate ? (
                    <span className="inline-flex items-center gap-1">
                      <ShieldCheck size={11} /> Gate
                    </span>
                  ) : isApproval ? (
                    <span className="inline-flex items-center gap-1 text-violet-500">
                      <UserCheck size={11} /> Approval
                    </span>
                  ) : (
                    `Step ${s.position + 1}`
                  )}
                </span>
                <span className="truncate text-sm font-medium text-fg">{s.label || "Step"}</span>
                {s.replay ? (
                  <span className="inline-flex items-center gap-1 rounded bg-indigo-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-indigo-500">
                    <RotateCw size={9} />
                    replay{s.attempt > 1 ? ` ${s.attempt}` : ""}
                  </span>
                ) : (
                  s.attempt > 1 && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-amber-500">
                      attempt {s.attempt}
                    </span>
                  )
                )}
                <span
                  className={`rounded px-1.5 py-0.5 text-[9px] font-semibold ${meta.chip}`}
                >
                  {meta.label}
                </span>
                {isGate && s.gate_verdict && (
                  <span
                    className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${gateVerdictChip(s.gate_verdict)}`}
                  >
                    {s.gate_verdict}
                  </span>
                )}
                <span className="ml-auto flex items-center gap-2 text-[10px] text-muted">
                  {tokens > 0 && (
                    <span
                      className="flex items-center gap-0.5 tabular-nums"
                      data-tooltip={`${(s.input_tokens || 0).toLocaleString()} in · ${(s.output_tokens || 0).toLocaleString()} out`}
                    >
                      <Coins size={10} />
                      {formatTokens(tokens)}
                    </span>
                  )}
                  {duration && <span className="tabular-nums">{duration}</span>}
                  {s.finished_at && <span>{workflowRelativeTime(s.finished_at)}</span>}
                  {/* Run this one step again on the exact input it saw. Offered
                      on any finished agent-backed attempt — including one that
                      succeeded, which is the point: it's how you get a second
                      take without re-running (and re-paying for) the pipeline.
                      An approval checkpoint ran no agent, so it has nothing to
                      replay. */}
                  {onReplay && s.agent_id != null && !running && s.finished_at != null && (
                    <button
                      type="button"
                      onClick={() => void replay(s.id)}
                      disabled={replayingId != null}
                      data-tooltip={"Replay this step on the same input\nNothing after it runs"}
                      aria-label={`Replay ${s.label || "this step"}`}
                      className="text-muted transition hover:text-indigo-500 disabled:opacity-40"
                    >
                      {replayingId === s.id ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <RotateCw size={12} />
                      )}
                    </button>
                  )}
                  {/* Only a *reusable* agent has somewhere to open: an inline
                      step's vessel is hidden from the Agents section, and an
                      approval never had an agent at all. */}
                  {onOpenAgent && s.agent_id != null && !s.agent_inline && (
                    <button
                      type="button"
                      onClick={() => onOpenAgent(s.agent_id as number)}
                      className="text-indigo-500 transition hover:underline"
                    >
                      Open
                    </button>
                  )}
                </span>
              </div>

              {/* Input the step received */}
              {s.input_context && (
                <details className="group mt-2">
                  <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] font-medium text-muted transition hover:text-fg">
                    <ArrowDownToLine size={12} className="text-sky-500" />
                    Input received
                  </summary>
                  <div className="mt-1.5 max-h-52 overflow-y-auto rounded-lg border border-border bg-bg/40 px-2.5 py-2">
                    <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-fg/80">
                      {s.input_context}
                    </p>
                  </div>
                </details>
              )}

              {/* Output the step produced */}
              {s.output_summary && (
                <details className="group mt-1.5" open={!isGate}>
                  <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] font-medium text-muted transition hover:text-fg">
                    <ArrowUpToLine size={12} className="text-emerald-500" />
                    {isGate ? "Verdict rationale" : "Output"}
                  </summary>
                  <div className="mt-1.5 rounded-lg border border-border bg-bg/40 px-2.5 py-2">
                    <CopyableMarkdown>{s.output_summary}</CopyableMarkdown>
                  </div>
                </details>
              )}

              {running && !s.output_summary && (
                <p className="mt-1.5 text-[11px] italic text-muted">Working…</p>
              )}

              {/* How it got there. Only an agent-backed attempt has activity —
                  an approval checkpoint never ran one. */}
              {s.agent_id != null && <StepActivity workflowId={workflowId} step={s} />}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
