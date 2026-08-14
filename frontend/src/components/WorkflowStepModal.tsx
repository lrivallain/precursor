import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  FileText,
  History,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type { AgentSession, WorkflowRun, WorkflowRunStep, WorkflowStep } from "../lib/types";
import { Modal } from "./Modal";
import { AgentStatusBadge } from "./AgentStatusBadge";
import { CopyableMarkdown } from "./CopyableMarkdown";
import {
  gateVerdictChip,
  stepElapsed,
  stepLabel,
  traceMeta,
  workflowRelativeTime,
} from "../lib/workflows";
import { agentRelativeTime } from "../lib/agents";

interface Props {
  step: WorkflowStep;
  /** Durable run history from the parent, used to show this step's input + past
   *  attempts without the modal fetching its own trace. */
  runs?: WorkflowRun[];
  /** Open the full Agents cockpit on this step's agent (deep link). */
  onOpenInAgents: (agentId: number) => void;
  onClose: () => void;
}

/**
 * Read-only detail for a single workflow step, shown as a modal so the board
 * stays live behind it. Shows what the step is, what it received, what it
 * produced and its attempt history.
 *
 * Deliberately **not** an editor: authoring a step happens in *Edit steps* on
 * the board, so a run you are inspecting can't be quietly mutated from the
 * record of it. For heavy inspection it deep-links to the Agents cockpit — but
 * only for a *reusable* agent, since an inline step's vessel isn't listed there
 * and an approval step has no agent at all.
 */
export function WorkflowStepModal({ step, runs = [], onOpenInAgents, onClose }: Props) {
  const agentId = step.agent_id;
  const [agent, setAgent] = useState<AgentSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);



  // Every recorded attempt of THIS step across all runs, newest first. Matched
  // by pipeline position (snapshotted into each run-step) so it survives agent
  // swaps, with agent_id as a fallback for legacy rows.
  const stepAttempts = useMemo<Array<{ run: WorkflowRun; rs: WorkflowRunStep }>>(() => {
    const out: Array<{ run: WorkflowRun; rs: WorkflowRunStep }> = [];
    for (const run of runs) {
      for (const rs of run.step_runs) {
        const matches =
          rs.position === step.position ||
          (rs.agent_id != null && rs.agent_id === step.agent_id);
        if (matches) out.push({ run, rs });
      }
    }
    out.sort((a, b) => {
      if (b.run.run_number !== a.run.run_number) return b.run.run_number - a.run.run_number;
      return b.rs.attempt - a.rs.attempt;
    });
    return out;
  }, [runs, step.position, step.agent_id]);

  // The input the most recent attempt actually received (what the step "saw").
  const latestInput = stepAttempts.find((a) => a.rs.input_context)?.rs.input_context ?? null;

  const load = useCallback(async () => {
    if (agentId == null) {
      // An approval checkpoint legitimately has no agent, so that is not an
      // error — it's explained inline and the step's own detail still renders.
      // A *missing* agent on any other kind is a genuine broken reference.
      setError(step.kind === "approval" ? null : "This step's agent was deleted.");
      setLoading(false);
      return;
    }
    try {
      const fresh = await api.agents.get(agentId);
      setAgent(fresh);
      setError(null);
    } catch {
      setError("Failed to load the agent.");
    } finally {
      setLoading(false);
    }
  }, [agentId, step.kind]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll while the agent is live so the modal tracks progress without wiring
  // its own SSE subscription (the board behind it already refreshes globally).
  useEffect(() => {
    const status = agent?.status;
    if (status !== "running" && status !== "pending" && status !== "needs_approval") return;
    const t = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(t);
  }, [agent?.status, load]);


  const progress = agent?.progress ?? null;
  const artifacts = useMemo(
    () => (agent?.artifacts ?? []).slice().sort((a, b) => b.id - a.id),
    [agent?.artifacts],
  );

  // The most recent recorded outcome for this step: for an approval that's the
  // reviewer's verdict and note, for anything else its last status.
  const latestDecision = stepAttempts.find((a) => a.rs.finished_at)?.rs ?? null;

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      padded
      backdropClassName="bg-black/50 backdrop-blur-sm"
      panelClassName="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-indigo-500">
              Step {step.position + 1}
            </span>
            {step.kind === "approval" && (
              <span className="rounded bg-violet-500/15 px-1.5 py-0.5 text-[10px] font-medium text-violet-500">
                Approval
              </span>
            )}
            {step.kind === "gate" && (
              <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-500">
                Gate
              </span>
            )}
            {agent && <AgentStatusBadge status={agent.status} />}
          </div>
          <h2 className="mt-1 truncate text-base font-semibold text-fg">{stepLabel(step)}</h2>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {agentId != null && (
            <button
              type="button"
              onClick={() => void load()}
              className="rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
              data-tooltip="Refresh"
              aria-label="Refresh"
            >
              <RefreshCw size={15} />
            </button>
          )}
          {agentId != null && !agent?.inline && (
            <button
              type="button"
              onClick={() => onOpenInAgents(agentId)}
              className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs text-indigo-500 transition hover:bg-indigo-500/10"
              data-tooltip="Open in the Agents cockpit"
            >
              Open <ArrowUpRight size={13} />
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" /> Loading…
          </div>
        )}
        {error && !loading && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
            {error}
          </div>
        )}

        {/* --- Step-level detail. Renders for every kind, including an approval
            checkpoint: it has a brief, an input and a decision history just like
            any other step — it simply has no agent behind it. --- */}
        {!loading && step.kind === "approval" && (
          <p className="rounded-lg border border-violet-500/25 bg-violet-500/5 px-3 py-2 text-xs leading-relaxed text-violet-600/90 dark:text-violet-400/80">
            A human checkpoint: the run parks here until someone approves it. No
            agent runs, so there is no objective, no tools and no token cost.
          </p>
        )}

        {!loading && step.instructions && (
          <div>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
              {step.kind === "approval" ? "What the reviewer was asked" : "Step instructions"}
            </h3>
            <p className="whitespace-pre-wrap rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm text-fg/90">
              {step.instructions}
            </p>
          </div>
        )}

        {!loading && latestDecision && (
          <div>
            <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
              {step.kind === "approval" ? "Decision" : "Outcome"}
            </h3>
            <div className="rounded-lg border border-border bg-bg/40 px-3 py-2">
              <span
                className={`mb-1.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold ${traceMeta(latestDecision.status).chip}`}
              >
                {traceMeta(latestDecision.status).label}
              </span>
              {latestDecision.gate_verdict && (
                <span
                  className={`ml-1.5 rounded px-1.5 py-0.5 text-[10px] font-bold ${gateVerdictChip(latestDecision.gate_verdict)}`}
                >
                  {latestDecision.gate_verdict}
                </span>
              )}
              {latestDecision.output_summary ? (
                <CopyableMarkdown className="text-sm text-fg/90">
                  {latestDecision.output_summary}
                </CopyableMarkdown>
              ) : (
                <p className="text-[11px] text-muted">
                  {step.kind === "approval" ? "No note was left." : "No output recorded."}
                </p>
              )}
            </div>
          </div>
        )}

        {!loading && latestInput && (
          <div>
            <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              <ArrowDownToLine size={12} /> Input received
            </h3>
            <details className="group rounded-lg border border-border bg-bg/40">
              <summary className="cursor-pointer list-none px-3 py-2 text-[11px] text-muted transition group-open:text-fg">
                What this step was handed
              </summary>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-border px-3 py-2 text-[11px] leading-relaxed text-fg/80">
                {latestInput}
              </pre>
            </details>
          </div>
        )}

        {!loading && stepAttempts.length > 0 && (
          <div>
            <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
              <History size={12} /> Run history
              <span className="rounded-full bg-white/5 px-1.5 text-[10px]">
                {stepAttempts.length}
              </span>
            </h3>
            <div className="space-y-2">
              {stepAttempts.map(({ run, rs }) => {
                const meta = traceMeta(rs.status);
                const elapsed = stepElapsed(rs);
                return (
                  <details
                    key={`${run.id}-${rs.id}`}
                    className="group rounded-lg border border-border bg-bg/40"
                  >
                    <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-xs">
                      <span className="font-medium text-fg">Run #{run.run_number}</span>
                      {rs.attempt > 1 && (
                        <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-500">
                          attempt {rs.attempt}
                        </span>
                      )}
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${meta.chip}`}>
                        {meta.label}
                      </span>
                      {rs.gate_verdict && (
                        <span
                          className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${gateVerdictChip(rs.gate_verdict)}`}
                        >
                          {rs.gate_verdict}
                        </span>
                      )}
                      {elapsed && (
                        <span className="tabular-nums text-[10px] text-muted">{elapsed}</span>
                      )}
                      <span className="ml-auto text-[10px] text-muted">
                        {workflowRelativeTime(rs.finished_at ?? rs.started_at)}
                      </span>
                    </summary>
                    <div className="space-y-2 border-t border-border px-3 py-2">
                      {rs.output_summary ? (
                        <CopyableMarkdown>{rs.output_summary}</CopyableMarkdown>
                      ) : (
                        <p className="text-[11px] text-muted">No output recorded.</p>
                      )}
                    </div>
                  </details>
                );
              })}
            </div>
          </div>
        )}

        {/* --- Agent-only detail. Absent for an approval checkpoint. --- */}
        {agent && !loading && (
          <>
            {progress != null && (
              <div>
                <div className="mb-1 flex items-center justify-between text-[11px] text-muted">
                  <span>{agent.progress_label || "Progress"}</span>
                  <span className="tabular-nums">{Math.round(progress)}%</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/5">
                  <div
                    className="h-full rounded-full bg-indigo-500 transition-all"
                    style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                  />
                </div>
              </div>
            )}

            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                Objective
              </h3>
              <p className="whitespace-pre-wrap text-sm text-fg/90">{agent.task_prompt}</p>
            </div>

            {artifacts.length > 0 && (
              <div>
                <h3 className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  <FileText size={12} /> Artifacts
                  <span className="rounded-full bg-white/5 px-1.5 text-[10px]">
                    {artifacts.length}
                  </span>
                </h3>
                <div className="space-y-2">
                  {artifacts.map((a) => (
                    <details key={a.id} className="group rounded-lg border border-border bg-bg/40">
                      <summary className="cursor-pointer list-none px-3 py-2 text-xs font-medium text-fg">
                        {a.title}
                      </summary>
                      <div className="border-t border-border px-3 py-2">
                        <CopyableMarkdown>{a.content}</CopyableMarkdown>
                      </div>
                    </details>
                  ))}
                </div>
              </div>
            )}

            {agent.error && (
              <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-500">
                {agent.error}
              </div>
            )}

            <p className="text-[10px] text-muted">
              Updated {agentRelativeTime(agent.updated_at)}
            </p>
          </>
        )}
      </div>
    </Modal>
  );
}
