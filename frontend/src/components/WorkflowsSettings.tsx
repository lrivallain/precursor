import { useState } from "react";
import { HardDrive, Loader2, Workflow as WorkflowIcon } from "lucide-react";
import { api } from "../lib/api";
import { settingsStore, useSettings } from "../lib/settingsStore";

/**
 * Settings for Workflows mode — the *defaults* a new workflow and its steps
 * start from. Everything here stays overridable per workflow and per step; this
 * only decides where a fresh one begins, so an operator whose pipelines are
 * mostly text transforms doesn't have to switch the same toggles by hand every
 * time. The pipelines themselves live in the "Workflows" sidebar mode.
 */

const CAPABILITIES: {
  key: "workflows_default_use_mcp" | "workflows_default_use_skills" | "workflows_default_use_memory";
  label: string;
  hint: string;
}[] = [
  {
    key: "workflows_default_use_mcp",
    label: "Tools (MCP)",
    hint: "Tool schemas are a large fixed cost paid on every turn. Off by default suits pipelines that mostly transform text.",
  },
  {
    key: "workflows_default_use_skills",
    label: "Skills",
    hint: "Whether a new step may reach for a stored skill rather than solving the task directly.",
  },
  {
    key: "workflows_default_use_memory",
    label: "Memory",
    hint: "Whether long-term memory is injected as standing context. A pure transform step rarely needs it.",
  },
];

function clampDays(raw: string): number {
  return Math.min(3650, Math.max(0, Math.round(Number(raw) || 0)));
}

export function WorkflowsSettings({ onOpenMigration }: { onOpenMigration?: () => void } = {}) {
  const settings = useSettings();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const timeoutSeconds = settings?.workflows_default_step_timeout_seconds ?? 0;
  const agentsEnabled = settings?.agents_enabled ?? false;
  const stepRetentionDays = settings?.workflow_step_retention_days ?? 30;

  async function patch(update: Record<string, boolean | number>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      settingsStore.set(await api.settings.update(update));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-2">
        <WorkflowIcon size={16} className="mt-0.5 text-accent" />
        <p className="text-[12px] text-muted">
          Defaults for new workflows and their steps. Every one of these can be
          overridden on an individual workflow or step — this only sets the
          starting point.
        </p>
      </div>

      {/* Not hidden, unlike the Agents panel's runtime-dependent controls: these
          are plain preferences that stay meaningful to set ahead of time, and
          they survive the toggle. What they must not do is imply a workflow
          could run right now — so say what the Workflows section itself says. */}
      {!agentsEnabled && (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          <span className="font-medium">Workflows need Agents mode.</span> A
          workflow chains agents into a repeatable pipeline, so it runs on the
          same runtime. These defaults are saved either way and apply once you
          turn Agents on in <span className="font-medium">Settings → Agents</span>.
        </p>
      )}

      <section className="space-y-2">
        <h3 className="text-sm font-medium">What a new step may use</h3>
        {CAPABILITIES.map((cap) => {
          const enabled = (settings?.[cap.key] as boolean | undefined) ?? true;
          return (
            <label key={cap.key} className="block space-y-1">
              <span className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enabled}
                  disabled={busy}
                  onChange={(e) => void patch({ [cap.key]: e.target.checked })}
                />
                {cap.label}
              </span>
              <span className="block pl-6 text-[11px] text-muted">{cap.hint}</span>
            </label>
          );
        })}
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-medium">Stall watchdog</h3>
        <label className="block space-y-1">
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              step={1}
              disabled={busy}
              value={Math.max(0, Math.round(timeoutSeconds / 60))}
              onChange={(e) => {
                const minutes = Math.max(0, Number(e.target.value) || 0);
                settingsStore.set({
                  ...settings!,
                  workflows_default_step_timeout_seconds: minutes * 60,
                });
              }}
              onBlur={(e) => {
                const minutes = Math.max(0, Number(e.target.value) || 0);
                void patch({ workflows_default_step_timeout_seconds: minutes * 60 });
              }}
              className="w-20 rounded border border-border bg-surface px-2 py-1 text-[12px]"
            />
            <span className="text-[12px] text-muted">minutes</span>
          </div>
          <span className="block text-[11px] text-muted">
            How long a step in a new workflow may run before it&apos;s declared
            stuck: its agent is stopped and the step&apos;s failure policy applies.
            <strong className="text-fg/80"> 0 disables it</strong>, which is the
            safe default — a long-running step is not necessarily a wedged one.
          </span>
        </label>
      </section>

      {/* Always shown: the sweep is gated on the scheduler, not on Agents mode,
          so it keeps clearing archived step inputs after the toggle goes off. */}
      <div className="space-y-2 rounded border border-border bg-surface/50 p-3">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <HardDrive size={14} /> Step input retention
        </span>
        <p className="text-[11px] text-muted">
          Each step archives the full prompt it was handed, which adds up fast
          when steps chain large outputs. Once a run has finished this long ago,
          those prompts are replaced with a placeholder. A step keeps its output,
          status and token counts either way, but can no longer be replayed.
        </p>
        <div className="flex flex-wrap gap-4">
        <label className="block space-y-1">
          <span className="block text-[12px]">Keep step inputs for</span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={3650}
              step={1}
              disabled={busy}
              value={stepRetentionDays}
              onChange={(e) =>
                settingsStore.set({
                  ...settings!,
                  workflow_step_retention_days: clampDays(e.target.value),
                })
              }
              onBlur={(e) =>
                void patch({ workflow_step_retention_days: clampDays(e.target.value) })
              }
              className="w-20 rounded border border-border bg-surface px-2 py-1 text-[12px]"
            />
            <span className="text-[12px] text-muted">days</span>
          </div>
          <span className="block text-[11px] text-muted">
            0 keeps them forever.
          </span>
        </label>
        </div>
        <p className="text-[11px] text-muted">
          Settings → Usage stats shows what a sweep would free and runs it on
          demand.
        </p>
      </div>

      {onOpenMigration && (
        <section className="space-y-1">
          <h3 className="text-sm font-medium">Definition files</h3>
          <p className="text-[11px] text-muted">
            Declare workflows and their agents in YAML files instead of the database (work in
            progress).{" "}
            <button
              type="button"
              onClick={onOpenMigration}
              className="text-accent hover:underline"
            >
              Open the migration wizard
            </button>
          </p>
        </section>
      )}

      {error && <p className="text-[12px] text-red-500">{error}</p>}
      {busy && (
        <p className="flex items-center gap-1.5 text-[12px] text-muted">
          <Loader2 size={12} className="animate-spin" /> Saving…
        </p>
      )}
    </div>
  );
}
