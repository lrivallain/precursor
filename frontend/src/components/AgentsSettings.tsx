import { useCallback, useEffect, useState } from "react";
import { Bot, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { settingsStore, useSettings } from "../lib/settingsStore";
import { useConfirm } from "./ConfirmDialog";
import type { AgentModelInfo, AgentPermissionGrant } from "../lib/types";

// Settings-only controls for Agents mode. The actual agent UI (session list and
// workflow) lives in the top-level "Agents" sidebar mode, not here.
export function AgentsSettings() {
  const settings = useSettings();
  const confirmAction = useConfirm();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [models, setModels] = useState<AgentModelInfo[]>([]);
  const [grants, setGrants] = useState<AgentPermissionGrant[]>([]);

  const enabled = settings?.agents_enabled ?? false;
  const available = settings?.agents_available ?? false;
  const reason = settings?.agents_unavailable_reason ?? null;
  const defaultModel = settings?.agents_default_model ?? "";

  const loadGrants = useCallback(() => {
    if (!enabled) {
      setGrants([]);
      return;
    }
    void api
      .listAgentPermissions()
      .then(setGrants)
      .catch(() => setGrants([]));
  }, [enabled]);

  // Load the runtime's model list when the feature is on and available. Empty
  // when the runtime is down — we fall back to free text in that case.
  useEffect(() => {
    if (!enabled || !available) {
      setModels([]);
      return;
    }
    void api
      .listAgentModels()
      .then(setModels)
      .catch(() => setModels([]));
  }, [enabled, available]);

  useEffect(() => loadGrants(), [loadGrants]);

  async function resetPermissions(): Promise<void> {
    if (
      !(await confirmAction({
        message:
          "Revoke all “approve for session” grants? Running agents are reset and " +
          "will ask for permission again on their next action.",
        confirmLabel: "Revoke all",
        variant: "danger",
      }))
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.resetAgentPermissions();
      loadGrants();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function patch(update: {
    agents_enabled?: boolean;
    agents_default_model?: string;
  }): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      settingsStore.set(await api.updateSettings(update));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-3">
      <h3 className="flex items-center gap-1.5 text-sm font-medium">
        <Bot size={15} /> Agents mode
      </h3>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          disabled={busy}
          onChange={(e) => void patch({ agents_enabled: e.target.checked })}
          className="mt-0.5 accent-accent"
        />
        <span>
          <span className="flex items-center gap-1.5 text-sm">
            Enable Agents mode
            {busy && <Loader2 size={12} className="animate-spin text-muted" />}
          </span>
          <span className="block text-[11px] text-muted">
            Run long-running, autonomous Copilot agent tasks on demand. Once
            enabled, an “Agents” tab appears in the sidebar where you can start,
            follow, and attach agent sessions to a topic or chat.
          </span>
        </span>
      </label>

      {error && <p className="text-[11px] text-red-500">{error}</p>}

      {enabled && !available && (
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          The Copilot runtime isn&apos;t available yet
          {reason ? `: ${reason}` : "."} The feature is on, but agent tasks become
          runnable once the runtime is installed.
        </div>
      )}

      {enabled && available && (
        <div className="rounded border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[11px] text-emerald-700 dark:text-emerald-300">
          The Copilot runtime is available. Open the Agents tab in the sidebar to
          start a task.
        </div>
      )}

      {enabled && (
        <label className="block space-y-1">
          <span className="block text-sm">Default model</span>
          {models.length > 0 ? (
            <select
              value={defaultModel}
              disabled={busy}
              onChange={(e) => void patch({ agents_default_model: e.target.value })}
              className="w-full rounded border border-border bg-surface px-2 py-1.5 text-sm"
            >
              <option value="">Runtime default</option>
              {/* Keep the saved value selectable even if the runtime no longer lists it. */}
              {defaultModel && !models.some((m) => m.id === defaultModel) && (
                <option value={defaultModel}>{defaultModel}</option>
              )}
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={defaultModel}
              disabled={busy}
              placeholder="e.g. claude-sonnet-4.5"
              onChange={(e) =>
                settingsStore.set({
                  ...settings!,
                  agents_default_model: e.target.value,
                })
              }
              onBlur={(e) => void patch({ agents_default_model: e.target.value.trim() })}
              className="w-full rounded border border-border bg-surface px-2 py-1.5 text-sm"
            />
          )}
          <span className="block text-[11px] text-muted">
            Model used for new agent sessions when none is specified.
          </span>
        </label>
      )}

      {enabled && (
        <div className="space-y-2 rounded border border-border bg-surface/50 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              <ShieldCheck size={14} /> Session permissions
            </span>
            <button
              type="button"
              onClick={() => void resetPermissions()}
              disabled={busy || grants.length === 0}
              className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-red-500 disabled:opacity-40"
            >
              <Trash2 size={12} /> Reset all
            </button>
          </div>
          <p className="text-[11px] text-muted">
            “Approve for session” grants currently active in running agents. They
            reset automatically when an agent session ends; use Reset all to revoke
            them now.
          </p>
          {grants.length === 0 ? (
            <p className="text-[11px] text-muted">No active grants.</p>
          ) : (
            <ul className="space-y-1">
              {grants.map((g, i) => (
                <li
                  key={i}
                  className="flex items-baseline justify-between gap-2 rounded border border-border bg-bg px-2 py-1 text-[11px]"
                >
                  <span className="min-w-0">
                    <span className="font-medium">{g.title || g.type}</span>
                    {g.target && (
                      <span className="ml-1 break-all font-mono text-muted">{g.target}</span>
                    )}
                  </span>
                  <span className="shrink-0 text-muted">agent #{g.agent_id}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
