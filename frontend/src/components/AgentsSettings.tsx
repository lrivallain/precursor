import { useCallback, useEffect, useState } from "react";
import { Bot, HardDrive, Loader2, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";
import { RefineTextarea } from "./RefineTextarea";
import { settingsStore, useSettings } from "../lib/settingsStore";
import { useConfirm } from "./ConfirmDialog";
import { AgentBlueprintsSection } from "./AgentBlueprints";
import type { AgentApprovalPolicy, AgentModelInfo, AgentPermissionGrant } from "../lib/types";

// Approval policies, ordered most → least cautious, for the settings dropdown.
// Exported so per-agent selectors (create form + settings drawer) reuse the
// exact same wording as the global default control.
export const APPROVAL_POLICIES: {
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

// Clamp a number input's raw value to the range the backend enforces, so an
// out-of-range or empty entry commits the nearest legal value rather than NaN.
function clampInt(raw: string, min: number, max: number): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, Math.round(n)));
}

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
  const runtimeStarted = settings?.agents_runtime_started ?? false;
  const reason = settings?.agents_unavailable_reason ?? null;
  const defaultModel = settings?.agents_default_model ?? "";
  const approvalPolicy: AgentApprovalPolicy = settings?.agents_approval_policy ?? "balanced";
  const systemPrompt = settings?.agents_system_prompt ?? "";
  const watchdogTimeout = settings?.agents_watchdog_timeout_seconds ?? 600;
  // Archived-timeline retention. Deliberately readable even when Agents mode is
  // off: the events outlive the feature toggle, and the sweep keeps running.
  const eventRetentionDays = settings?.agent_event_retention_days ?? 30;
  const eventMaxPerSession = settings?.agent_event_max_per_session ?? 2000;

  const loadGrants = useCallback(() => {
    if (!enabled) {
      setGrants([]);
      return;
    }
    void api.agents.listPermissions()
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
    void api.agents.listModels()
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
      await api.agents.resetPermissions();
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
    agent_event_retention_days?: number;
    agent_event_max_per_session?: number;
  }): Promise<void> {
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

      {enabled && available && !runtimeStarted && (
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300">
          The Copilot runtime is installed but didn&apos;t start in this process —
          agents won&apos;t be driven until it&apos;s (re)started. This usually means
          the SDK client failed to launch (often after a dev auto-reload). Restart
          the server to recover; any agents left mid-turn are reset to
          <span className="font-medium"> Interrupted</span> so you can Resume them.
        </div>
      )}

      {enabled && available && runtimeStarted && (
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
          <RefineTextarea
            value={systemPrompt}
            disabled={busy}
            rows={4}
            refineKind="system_prompt"
            placeholder="Extra instructions appended to every agent session…"
            onValueChange={(v) =>
              settingsStore.set({ ...settings!, agents_system_prompt: v })
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

      <div className="space-y-2 rounded border border-border bg-surface/50 p-3">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          <HardDrive size={14} /> Timeline retention
        </span>
        <p className="text-[11px] text-muted">
          Every event an agent emits is archived so its timeline survives a
          restart, which makes it the fastest-growing table in a busy install.
          These two levers bound it. An agent keeps its result, artifacts, state
          and posted messages either way, and a <em>running</em> agent is never
          pruned.
        </p>
        <div className="flex flex-wrap gap-4">
          <label className="block space-y-1">
            <span className="block text-[12px]">Keep events for</span>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={3650}
                step={1}
                disabled={busy}
                value={eventRetentionDays}
                onChange={(e) =>
                  settingsStore.set({
                    ...settings!,
                    agent_event_retention_days: clampInt(e.target.value, 0, 3650),
                  })
                }
                onBlur={(e) =>
                  void patch({
                    agent_event_retention_days: clampInt(e.target.value, 0, 3650),
                  })
                }
                className="w-20 rounded border border-border bg-surface px-2 py-1 text-[12px]"
              />
              <span className="text-[12px] text-muted">days</span>
            </div>
            <span className="block text-[11px] text-muted">
              0 keeps them forever.
            </span>
          </label>

          <label className="block space-y-1">
            <span className="block text-[12px]">Max events per agent</span>
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={0}
                max={1000000}
                step={100}
                disabled={busy}
                value={eventMaxPerSession}
                onChange={(e) =>
                  settingsStore.set({
                    ...settings!,
                    agent_event_max_per_session: clampInt(e.target.value, 0, 1000000),
                  })
                }
                onBlur={(e) =>
                  void patch({
                    agent_event_max_per_session: clampInt(e.target.value, 0, 1000000),
                  })
                }
                className="w-24 rounded border border-border bg-surface px-2 py-1 text-[12px]"
              />
              <span className="text-[12px] text-muted">events</span>
            </div>
            <span className="block text-[11px] text-muted">
              Newest kept first; 0 is unlimited.
            </span>
          </label>
        </div>
        <p className="text-[11px] text-muted">
          Agent traffic is bursty rather than aged, so the per-agent cap is what
          bounds a single long autonomous run — the window alone wouldn&apos;t
          reach it for weeks. Settings → Usage stats shows what a sweep would
          free and runs it on demand.
        </p>
      </div>

      <div className="border-t border-border pt-4">
        <AgentBlueprintsSection />
      </div>
    </section>
  );
}
