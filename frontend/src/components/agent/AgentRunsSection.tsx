import { History } from "lucide-react";
import { describeRun, RUN_TRIGGER_LABEL } from "../../lib/agentCockpitModel";
import type { AgentRun, AgentSession } from "../../lib/types";

function runButtonClass(active: boolean, current: boolean): string {
  if (active) return "border-accent bg-accent/10";
  if (current) return "border-accent/50 bg-accent/5 hover:bg-accent/10";
  return "border-border bg-surface/50 hover:bg-surface";
}

function RunButton({
  run,
  current,
  active,
  onSelect,
}: {
  run: AgentRun;
  current: boolean;
  active: boolean;
  onSelect: (choice: number | "all") => void;
}) {
  const spend = run.total_input_tokens + run.total_output_tokens;
  return (
    <button
      type="button"
      onClick={() => onSelect(active ? "all" : run.id)}
      aria-pressed={active}
      className={`w-full rounded border px-2 py-1 text-left transition-colors ${runButtonClass(active, current)}`}
      data-tooltip={`${describeRun(run)}\n${run.total_input_tokens.toLocaleString()} in · ${run.total_output_tokens.toLocaleString()} out\n${
        active ? "Click to show every run" : "Click to read this run on its own"
      }`}
    >
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-[11px] font-medium">
          {RUN_TRIGGER_LABEL[run.trigger]}
          {run.workflow_run_id ? ` · #${run.workflow_run_id}` : ""}
        </span>
        {current && <span className="shrink-0 rounded bg-accent/20 px-1 text-[10px] text-accent">now</span>}
      </div>
      <div className="flex items-center gap-1.5 text-[10px] text-muted">
        <span className="truncate">{run.status}</span>
        {spend > 0 && <span className="shrink-0">· {spend.toLocaleString()} tok</span>}
      </div>
    </button>
  );
}

// Execution history for one agent.
//
// An agent is a reusable *definition*, so it can be driven by several things at
// once — two workflows, a schedule and a manual nudge. Each of those is a run
// with its own status, capability snapshot, artifacts and token spend, and this
// is where that separation becomes visible: without it a shared agent looks like
// a single confusing timeline where concurrent drivers appear to overwrite
// each other.
export function AgentRunsSection({
  agent,
  runs,
  selected,
  onSelect,
}: {
  agent: AgentSession;
  runs: AgentRun[];
  selected: number | null;
  onSelect: (choice: number | "all") => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 text-sm font-medium">
        <History size={14} />
        <span>Runs</span>
        {runs.length > 0 && <span className="text-[11px] text-muted">({runs.length})</span>}
      </div>
      {runs.length === 0 ? (
        <p className="text-[11px] text-muted">
          No executions yet. Each start — manual, workflow, schedule or webhook — opens its own run
          so concurrent drivers stay separate.
        </p>
      ) : (
        <>
          <button
            type="button"
            onClick={() => onSelect("all")}
            className={`w-full rounded border px-2 py-1 text-left text-[11px] ${
              selected == null
                ? "border-accent/50 bg-accent/10 font-medium"
                : "border-border bg-surface/50 text-muted hover:bg-surface"
            }`}
            data-tooltip="Show every execution in one timeline"
          >
            All runs
          </button>
          {runs.map((run) => (
            <RunButton
              key={run.id}
              run={run}
              current={agent.current_run?.id === run.id}
              active={selected === run.id}
              onSelect={onSelect}
            />
          ))}
        </>
      )}
    </div>
  );
}
