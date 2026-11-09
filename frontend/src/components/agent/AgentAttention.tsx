import { AlertTriangle, CornerDownRight, HelpCircle, History, PauseCircle, PlayCircle, Radar, ShieldQuestion } from "lucide-react";
import type { AgentSession } from "../../lib/types";
import type { AgentPane } from "../AgentResults";

function ApprovalNotice({ pane, onReview }: { pane: AgentPane; onReview: () => void }) {
  return (
    <div className="mb-3 flex items-center gap-1.5 rounded border border-orange-500/30 bg-orange-500/10 px-2 py-1 text-[11px] text-orange-600 dark:text-orange-400">
      <ShieldQuestion size={13} className="shrink-0" />
      <span className="min-w-0 flex-1">
        Waiting for your approval
        {pane === "activity" ? " — see the highlighted step below." : "."}
      </span>
      {pane === "result" && (
        <button
          type="button"
          onClick={onReview}
          className="shrink-0 rounded px-1.5 py-0.5 font-medium hover:bg-orange-500/15"
        >
          Review
        </button>
      )}
    </div>
  );
}

function QuestionNotice({ question, onAnswer }: { question: string | null; onAnswer: () => void }) {
  return (
    <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-700 dark:text-amber-300">
      <div className="flex items-center gap-1.5 font-semibold">
        <HelpCircle size={13} className="shrink-0" /> The agent needs your input
      </div>
      {question && <p className="mt-1 leading-relaxed text-text opacity-90">{question}</p>}
      <button
        type="button"
        onClick={onAnswer}
        className="mt-2 inline-flex items-center gap-1 rounded-md bg-amber-500/20 px-2 py-1 font-medium text-amber-900 transition hover:bg-amber-500/30 dark:text-amber-100"
      >
        <CornerDownRight size={12} /> Answer
      </button>
    </div>
  );
}

function ParkedNotice({ disabled, onStartNow }: { disabled: boolean; onStartNow: () => void }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2 rounded border border-slate-500/30 bg-slate-500/10 px-2 py-1.5 text-[11px] text-slate-600 dark:text-slate-300">
      <span className="flex items-center gap-1.5">
        <PauseCircle size={13} /> Parked — armed for a trigger (a parent
        finishing, a webhook, or a manual start).
      </span>
      <button
        type="button"
        onClick={onStartNow}
        disabled={disabled}
        className="flex items-center gap-1 rounded bg-slate-500/20 px-2 py-0.5 font-medium text-slate-800 hover:bg-slate-500/30 disabled:opacity-50 dark:text-slate-200"
      >
        <PlayCircle size={12} /> Start now
      </button>
    </div>
  );
}

function InterruptedNotice({ canResume, disabled, onResume }: { canResume: boolean; disabled: boolean; onResume: () => void }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-700 dark:text-amber-400">
      <span className="flex items-center gap-1.5">
        <AlertTriangle size={13} /> This turn was interrupted before it
        finished.
      </span>
      {canResume && (
        <button
          type="button"
          onClick={onResume}
          disabled={disabled}
          className="flex items-center gap-1 rounded bg-amber-500/20 px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-500/30 disabled:opacity-50 dark:text-amber-300"
        >
          <PlayCircle size={12} /> Resume
        </button>
      )}
    </div>
  );
}

/**
 * What needs you — an approval, a question, a parked or interrupted run, a
 * failure. Shown on both panes: a result on screen mustn't hide the Resume
 * button or the question the agent is waiting on.
 */
export function AgentAttention({
  agent,
  pane,
  actionsDisabled,
  onReview,
  onAnswer,
  onStartNow,
  onResume,
}: {
  agent: AgentSession;
  pane: AgentPane;
  /** Busy, or the runtime isn't ready. */
  actionsDisabled: boolean;
  onReview: () => void;
  onAnswer: () => void;
  onStartNow: () => void;
  onResume: () => void;
}) {
  const status = agent.status;
  return (
    <>
      {status === "needs_approval" && <ApprovalNotice pane={pane} onReview={onReview} />}
      {status === "blocked" && <QuestionNotice question={agent.blocked_question} onAnswer={onAnswer} />}
      {status === "waiting" && <ParkedNotice disabled={actionsDisabled} onStartNow={onStartNow} />}
      {status === "interrupted" && (
        <InterruptedNotice canResume={Boolean(agent.active_prompt)} disabled={actionsDisabled} onResume={onResume} />
      )}
      {agent.error && (
        <div className="mb-3 rounded border border-red-500/30 bg-red-500/10 p-2 text-[11px] text-red-500">
          {agent.error}
        </div>
      )}
    </>
  );
}

/**
 * The transcript is scoped to one execution. Shown in the transcript itself,
 * not only in the Runs rail, so the filter can't strand a user who has the
 * insights panel collapsed. Silent on a single-run agent, where "the latest
 * run" and "everything" are the same timeline.
 */
export function RunFilterNotice({
  runFilter,
  following,
  runCount,
  onShowAll,
}: {
  runFilter: number | null;
  /** The filter follows the newest run rather than a pick. */
  following: boolean;
  runCount: number;
  onShowAll: () => void;
}) {
  if (runFilter == null || runCount <= 1) return null;
  return (
    <div className="mb-3 flex items-center gap-1.5 rounded border border-accent/40 bg-accent/10 px-2 py-1 text-[11px]">
      <History size={13} className="shrink-0" />
      <span className="min-w-0 flex-1 truncate">
        {following
          ? `Showing the latest run (#${runFilter}). Earlier executions are hidden.`
          : `Showing run #${runFilter} only — other executions of this agent are hidden.`}
      </span>
      <button
        type="button"
        onClick={onShowAll}
        className="shrink-0 rounded px-1.5 py-0.5 font-medium text-accent hover:bg-accent/15"
      >
        Show all runs
      </button>
    </div>
  );
}

/**
 * Mission strip: objective + self-reported progress for autonomous agents, so
 * the transcript reads as a tracked mission, not a chat.
 */
export function MissionStrip({ agent }: { agent: AgentSession }) {
  if (!agent.autonomy_enabled || agent.status === "completed") return null;
  return (
    <div className="mb-3 rounded-lg border border-violet-500/30 bg-violet-500/[0.06] px-2.5 py-2">
      <div className="flex items-center gap-1.5 text-[11px] font-medium text-violet-600 dark:text-violet-300">
        <Radar size={13} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate">Objective: {agent.task_prompt}</span>
        <span className="shrink-0 rounded-full bg-violet-500/15 px-1.5 py-0.5 text-[10px] tabular-nums">
          step {agent.step_count}/{agent.max_steps}
        </span>
      </div>
      {agent.progress != null && (
        <div className="mt-1.5 flex flex-col gap-1">
          <div className="flex items-center justify-between text-[10px] text-muted">
            <span className="min-w-0 truncate">{agent.progress_label ?? "Progress"}</span>
            <span className="shrink-0 font-semibold tabular-nums">{agent.progress}%</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-border/60">
            <span
              className="block h-full rounded-full bg-violet-500 transition-all"
              style={{ width: `${Math.min(100, Math.max(0, agent.progress))}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
