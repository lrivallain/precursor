import {
  CalendarClock,
  Loader2,
  Pause,
  Play,
  Plus,
  Upload,
  Webhook,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { api } from "../lib/api";
import type { TransferImportResult, Workflow } from "../lib/types";
import {
  WORKFLOW_STATUS_BADGE,
  WORKFLOW_STATUS_DOT,
  WORKFLOW_STATUS_LABEL,
  scheduleSummary,
  stepLabel,
  stepProgress,
  workflowIsActive,
  workflowRelativeTime,
} from "../lib/workflows";
import { useState } from "react";
import { ImportDialog } from "./ImportDialog";

interface Props {
  workflows: Workflow[];
  loading: boolean;
  onOpen: (workflow: Workflow) => void;
  onNew: () => void;
  onImported: (result: TransferImportResult) => void;
  onChanged: (workflow: Workflow) => void;
}

/**
 * Gallery of workflow cards — the Workflows landing. Each card shows identity,
 * live status, a compact step trail with progress, schedule/webhook badges, and
 * inline run/pause quick controls. Clicking the body opens the detail board.
 */
export function WorkflowList({ workflows, loading, onOpen, onNew, onImported, onChanged }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [importing, setImporting] = useState(false);

  async function quick(id: number, fn: () => Promise<Workflow>): Promise<void> {
    setBusyId(id);
    try {
      onChanged(await fn());
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <div>
          <h1 className="text-lg font-semibold text-fg">Workflows</h1>
          <p className="text-xs text-muted">Reusable pipelines that chain your agents.</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setImporting(true)}
            className="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-muted transition hover:bg-white/5 hover:text-fg"
            data-tooltip="Import a workflow from a YAML file"
          >
            <Upload size={15} /> Import
          </button>
          <button
            type="button"
            onClick={onNew}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600"
          >
            <Plus size={15} /> New workflow
          </button>
        </div>
      </div>

      {importing && (
        <ImportDialog
          expect="workflow"
          onClose={() => setImporting(false)}
          onImported={onImported}
        />
      )}

      <div className="flex-1 overflow-y-auto px-5 py-5">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" /> Loading…
          </div>
        ) : workflows.length === 0 ? (
          <div className="mx-auto max-w-md rounded-2xl border border-dashed border-border py-16 text-center">
            <p className="text-4xl">⚙️</p>
            <h2 className="mt-3 text-base font-semibold text-fg">No workflows yet</h2>
            <p className="mx-auto mt-1 max-w-xs text-sm text-muted">
              Chain existing agents — or author new ones inline — into a repeatable, schedulable
              pipeline.
            </p>
            <button
              type="button"
              onClick={onNew}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600"
            >
              <Plus size={15} /> Create your first workflow
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {workflows.map((wf) => {
              const { done, total } = stepProgress(wf);
              const schedule = scheduleSummary(wf);
              const active = workflowIsActive(wf);
              return (
                <button
                  key={wf.id}
                  type="button"
                  onClick={() => onOpen(wf)}
                  className="group flex flex-col rounded-2xl border border-border bg-surface p-4 text-left transition hover:border-indigo-500/50 hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-2">
                      {wf.icon ? (
                        <span className="text-xl">{wf.icon}</span>
                      ) : (
                        <WorkflowIcon size={18} className="text-muted" />
                      )}
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-fg">{wf.name}</h3>
                        <span className="flex items-center gap-1 text-[10px] text-muted">
                          <span className={`h-1.5 w-1.5 rounded-full ${WORKFLOW_STATUS_DOT[wf.status]}`} />
                          {WORKFLOW_STATUS_LABEL[wf.status]}
                        </span>
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${WORKFLOW_STATUS_BADGE[wf.status]}`}
                    >
                      {total > 0 ? `${done}/${total}` : "empty"}
                    </span>
                  </div>

                  {wf.description && (
                    <p className="mt-2 line-clamp-2 text-xs text-muted">{wf.description}</p>
                  )}

                  {/* Step trail */}
                  {total > 0 && (
                    <div className="mt-3 flex items-center gap-1 overflow-hidden">
                      {wf.steps.slice(0, 4).map((s, i) => (
                        <span
                          key={s.id}
                          className="truncate rounded-md bg-white/5 px-1.5 py-0.5 text-[10px] text-muted"
                          style={{ maxWidth: "5.5rem" }}
                          data-tooltip={stepLabel(s)}
                        >
                          {i + 1}. {stepLabel(s)}
                        </span>
                      ))}
                      {total > 4 && (
                        <span className="text-[10px] text-muted">+{total - 4}</span>
                      )}
                    </div>
                  )}

                  {/* Footer */}
                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-3">
                    <div className="flex items-center gap-2 text-[10px] text-muted">
                      {schedule && (
                        <span className="flex items-center gap-1">
                          <CalendarClock size={11} /> {schedule}
                        </span>
                      )}
                      {wf.webhook_token && <Webhook size={11} />}
                      {!schedule && !wf.webhook_token && (
                        <span>
                          {wf.last_run_at
                            ? `Ran ${workflowRelativeTime(wf.last_run_at)}`
                            : "Never run"}
                        </span>
                      )}
                    </div>
                    {!active && wf.steps.length > 0 && (
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation();
                          void quick(wf.id, () => api.workflows.run(wf.id));
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.stopPropagation();
                            void quick(wf.id, () => api.workflows.run(wf.id));
                          }
                        }}
                        className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-indigo-500 transition hover:bg-indigo-500/10"
                      >
                        {busyId === wf.id ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Play size={12} />
                        )}
                        Run
                      </span>
                    )}
                    {wf.status === "running" && (
                      <span
                        role="button"
                        tabIndex={0}
                        onClick={(e) => {
                          e.stopPropagation();
                          void quick(wf.id, () => api.workflows.pause(wf.id));
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.stopPropagation();
                            void quick(wf.id, () => api.workflows.pause(wf.id));
                          }
                        }}
                        className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[11px] text-muted transition hover:bg-white/5"
                      >
                        {busyId === wf.id ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : (
                          <Pause size={12} />
                        )}
                        Pause
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
