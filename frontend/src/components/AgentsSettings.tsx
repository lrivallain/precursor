import { useCallback, useEffect, useState } from "react";
import { Bot, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";
import { settingsStore, useSettings } from "../lib/settingsStore";
import { useConfirm } from "./ConfirmDialog";
import type { AgentApprovalPolicy, AgentModelInfo, AgentPermissionGrant } from "../lib/types";

// Approval policies, ordered most → least cautious, for the settings dropdown.
const APPROVAL_POLICIES: {
  value: AgentApprovalPolicy;
  label: string;
  hint: string;
}[] = [
  {
    value: "manual",
    label: "Manual — ask before every action",
    hint: "Most cautious. The agent pauses for your approval on every tool call, including reads.",
  },
  {
    value: "balanced",
    label: "Balanced — auto-approve read-only (recommended)",
    hint: "Reads, URL fetches and read-only tools run automatically; writes, shell commands and other changes still need approval.",
  },
  {
    value: "autonomous",
    label: "Autonomous — auto-approve everything",
    hint: "No prompts: the agent runs every action on its own. Use only for trusted tasks.",
  },
];

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
  const approvalPolicy: AgentApprovalPolicy = settings?.agents_approval_policy ?? "balanced";
  const systemPrompt = settings?.agents_system_prompt ?? "";
  const watchdogTimeout = settings?.agents_watchdog_timeout_seconds ?? 600;

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
    agents_approval_policy?: AgentApprovalPolicy;
    agents_system_prompt?: string;
    agents_watchdog_timeout_seconds?: number;
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
            <Select
              value={defaultModel}
              disabled={busy}
              onChange={(v) => void patch({ agents_default_model: v })}
              ariaLabel="Default agent model"
              fullWidth
              options={[
                { value: "", label: "Runtime default" },
                // Keep the saved value selectable even if the runtime no longer lists it.
                ...(defaultModel && !models.some((m) => m.id === defaultModel)
                  ? [{ value: defaultModel, label: defaultModel }]
                  : []),
                ...models.map((m) => ({ value: m.id, label: m.name })),
              ]}
            />
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
        <label className="block space-y-1">
          <span className="block text-sm">Default approval policy</span>
          <Select
            value={approvalPolicy}
            disabled={busy}
            onChange={(v) => void patch({ agents_approval_policy: v as AgentApprovalPolicy })}
            ariaLabel="Default approval policy"
            fullWidth
            options={APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label }))}
          />
          <span className="block text-[11px] text-muted">
            {APPROVAL_POLICIES.find((p) => p.value === approvalPolicy)?.hint}
          </span>
        </label>
      )}

      {enabled && (
        <label className="block space-y-1">
          <span className="block text-sm">Custom system message</span>
          <textarea
            value={systemPrompt}
            disabled={busy}
            rows={4}
            placeholder="Extra instructions appended to every agent session…"
            onChange={(e) =>
              settingsStore.set({ ...settings!, agents_system_prompt: e.target.value })
            }
            onBlur={(e) => void patch({ agents_system_prompt: e.target.value })}
            className="w-full resize-y rounded border border-border bg-surface px-2 py-1.5 font-mono text-[12px] leading-snug"
          />
          <span className="block text-[11px] text-muted">
            Appended to the Copilot base prompt (which can't be overridden) and any
            topic binding. Applies to new agent sessions.
          </span>
        </label>
      )}

      {enabled && (
        <label className="block space-y-1">
          <span className="block text-sm">Idle / runaway watchdog</span>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={1}
              step={1}
              disabled={busy}
              value={Math.max(1, Math.round(watchdogTimeout / 60))}
              onChange={(e) => {
                const minutes = Math.max(1, Number(e.target.value) || 1);
                settingsStore.set({
                  ...settings!,
                  agents_watchdog_timeout_seconds: minutes * 60,
                });
              }}
              onBlur={(e) => {
                const minutes = Math.max(1, Number(e.target.value) || 1);
                void patch({ agents_watchdog_timeout_seconds: minutes * 60 });
              }}
              className="w-20 rounded border border-border bg-surface px-2 py-1 text-[12px]"
            />
            <span className="text-[12px] text-muted">minutes</span>
          </div>
          <span className="block text-[11px] text-muted">
            A running agent with no activity for longer than this is flipped to
            “interrupted” (you can resume it). Minimum 30 seconds.
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
