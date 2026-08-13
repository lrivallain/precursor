import { useState } from "react";
import { Loader2, Workflow as WorkflowIcon } from "lucide-react";
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

export function WorkflowsSettings() {
  const settings = useSettings();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const timeoutSeconds = settings?.workflows_default_step_timeout_seconds ?? 0;

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

      {error && <p className="text-[12px] text-red-500">{error}</p>}
      {busy && (
        <p className="flex items-center gap-1.5 text-[12px] text-muted">
          <Loader2 size={12} className="animate-spin" /> Saving…
        </p>
      )}
    </div>
  );
}
