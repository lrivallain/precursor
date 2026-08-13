import {
  ArrowDownToLine,
  ArrowUpToLine,
  Coins,
  Loader2,
  ShieldCheck,
  UserCheck,
} from "lucide-react";
import type { WorkflowRun } from "../lib/types";
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
  /** Deep-link into the full Agents cockpit for a trace's agent. */
  onOpenAgent?: (agentId: number) => void;
}

/**
 * Vertical, append-only trace of one run's step attempts. Each row shows what a
 * step *received* (input context) and *produced* (output), plus gate verdicts
 * and per-attempt duration — the durable, inspectable record the run picker
 * scrolls through. A gate loop-back appears as a fresh row with a bumped
 * attempt badge, so retries read as distinct entries rather than mutations.
 */
export function WorkflowRunTrace({ run, onOpenAgent }: Props) {
  if (run.step_runs.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-xs text-muted">
        This run has no recorded steps yet.
      </p>
    );
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
                {s.attempt > 1 && (
                  <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[9px] font-semibold text-amber-500">
                    attempt {s.attempt}
                  </span>
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
                      title={`${(s.input_tokens || 0).toLocaleString()} in · ${(s.output_tokens || 0).toLocaleString()} out`}
                    >
                      <Coins size={10} />
                      {formatTokens(tokens)}
                    </span>
                  )}
                  {duration && <span className="tabular-nums">{duration}</span>}
                  {s.finished_at && <span>{workflowRelativeTime(s.finished_at)}</span>}
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
            </div>
          </li>
        );
      })}
    </ol>
  );
}
