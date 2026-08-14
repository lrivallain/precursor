import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Database, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import type { WorkflowState } from "../lib/types";

// The pipeline's own memory: named values every step can read and write, kept
// across runs. Distinct from the run trace above it, which records what one
// execution did — this is what the workflow *carries forward*, so it's also the
// surface for seeding a first run's cursor and for resetting one that's wrong.
export function WorkflowStatePanel({ workflowId }: { workflowId: number }) {
  const [entries, setEntries] = useState<WorkflowState[]>([]);
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [draftKey, setDraftKey] = useState("");
  const [draftValue, setDraftValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    void api.workflows
      .listState(workflowId)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [workflowId]);

  useEffect(() => {
    load();
    // A running step can write state mid-run, so refresh on the same signal the
    // rest of the view uses rather than leaving a stale list on screen.
    return eventBus.subscribe((ev) => {
      if (ev.type === "workflow.changed") load();
    });
  }, [load]);

  async function save(): Promise<void> {
    const key = draftKey.trim().toLowerCase();
    if (!key) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await api.workflows.setState(workflowId, key, draftValue);
      setEntries((prev) => {
        const rest = prev.filter((e) => e.key !== saved.key);
        return [...rest, saved].sort((a, b) => a.key.localeCompare(b.key));
      });
      setDraftKey("");
      setDraftValue("");
      setAdding(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save that value");
    } finally {
      setBusy(false);
    }
  }

  async function remove(key: string): Promise<void> {
    setBusy(true);
    try {
      await api.workflows.deleteState(workflowId, key);
      setEntries((prev) => prev.filter((e) => e.key !== key));
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  async function reset(): Promise<void> {
    setBusy(true);
    try {
      await api.workflows.clearState(workflowId);
      setEntries([]);
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-6">
      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted transition hover:text-fg"
        >
          <Database size={12} />
          Pipeline state
          {entries.length > 0 && <span className="text-muted">({entries.length})</span>}
          <ChevronRight size={13} className={`transition-transform ${open ? "rotate-90" : ""}`} />
        </button>
        {open && (
          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={() => setAdding((v) => !v)}
              disabled={busy}
              className="rounded p-1 text-muted transition hover:bg-surface hover:text-fg disabled:opacity-50"
              data-tooltip="Add a value"
              aria-label="Add a value"
            >
              <Plus size={14} />
            </button>
            {entries.length > 0 && (
              <button
                type="button"
                onClick={() => void reset()}
                disabled={busy}
                className="rounded p-1 text-muted transition hover:bg-surface hover:text-red-500 disabled:opacity-50"
                data-tooltip="Reset pipeline state"
                aria-label="Reset pipeline state"
              >
                <RotateCcw size={14} />
              </button>
            )}
          </div>
        )}
      </div>

      {open && (
        <div className="space-y-2">
          <p className="text-[11px] text-muted">
            Values this workflow keeps <strong>across runs</strong>. A step writes one with{" "}
            <code className="font-mono">workflow_state_set</code>; any later step reads it back with{" "}
            <code className="font-mono">{"{{state.<key>}}"}</code> in its instructions.
          </p>

          {adding && (
            <div className="space-y-2 rounded-lg border border-border bg-surface/50 p-2">
              <input
                value={draftKey}
                onChange={(e) => setDraftKey(e.target.value)}
                placeholder="key (e.g. last_processed_id)"
                className="w-full rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] outline-none focus:border-accent"
              />
              <textarea
                value={draftValue}
                onChange={(e) => setDraftValue(e.target.value)}
                placeholder="value (JSON by convention)"
                rows={3}
                className="w-full resize-y rounded border border-border bg-bg px-2 py-1 font-mono text-[11px] outline-none focus:border-accent"
              />
              {error && <p className="text-[11px] text-red-500">{error}</p>}
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={busy || !draftKey.trim()}
                  className="rounded bg-accent px-2 py-1 text-[11px] font-medium text-white transition hover:opacity-90 disabled:opacity-50"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setAdding(false);
                    setError(null);
                  }}
                  className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-muted transition hover:bg-surface"
                >
                  <X size={12} /> Cancel
                </button>
              </div>
            </div>
          )}

          {entries.length === 0 && !adding ? (
            <p className="text-[11px] text-muted">
              Nothing saved yet. Seed a starting value here, or let a step write one on its first
              run.
            </p>
          ) : (
            entries.map((entry) => (
              <div key={entry.key} className="rounded-lg border border-border bg-surface/50">
                <div className="flex items-center gap-1.5 px-2 py-1.5">
                  <button
                    type="button"
                    onClick={() => setExpanded((k) => (k === entry.key ? null : entry.key))}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                    aria-expanded={expanded === entry.key}
                    title={expanded === entry.key ? "Hide value" : "Show value"}
                  >
                    <ChevronRight
                      size={11}
                      className={`shrink-0 text-muted transition-transform ${
                        expanded === entry.key ? "rotate-90" : ""
                      }`}
                    />
                    <code className="min-w-0 flex-1 truncate font-mono text-[11px]">
                      {`{{state.${entry.key}}}`}
                    </code>
                    <span className="shrink-0 text-[10px] text-muted">{entry.value.length}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove(entry.key)}
                    disabled={busy}
                    className="rounded p-0.5 text-muted transition hover:text-red-500 disabled:opacity-50"
                    aria-label={`Delete ${entry.key}`}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
                {expanded === entry.key && (
                  <pre className="max-h-48 overflow-auto border-t border-border px-2 py-1.5 font-mono text-[10px] break-words whitespace-pre-wrap text-muted">
                    {entry.value || "(empty)"}
                  </pre>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
