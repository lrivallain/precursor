import { useCallback, useEffect, useState } from "react";
import { LayoutTemplate, Loader2, Play, Plus, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";
import { useConfirm } from "./ConfirmDialog";
import { APPROVAL_POLICIES } from "./AgentsSettings";
import type { AgentApprovalPolicy, AgentBlueprint } from "../lib/types";

// Reusable agent templates ("blueprints"): a saved task + governance profile you
// can stamp out into a fresh agent on demand. Managed from Settings so the
// create/new-agent flow stays uncluttered; instantiating one spawns a pending
// agent that the fleet/dashboard picks up like any other.
export function AgentBlueprintsSection({ onInstantiated }: { onInstantiated?: () => void }) {
  const confirmAction = useConfirm();
  const [blueprints, setBlueprints] = useState<AgentBlueprint[]>([]);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    void api.agents
      .listBlueprints()
      .then(setBlueprints)
      .catch(() => setBlueprints([]));
  }, []);

  useEffect(() => load(), [load]);

  async function instantiate(bp: AgentBlueprint): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await api.agents.instantiateBlueprint(bp.id, { start: true });
      onInstantiated?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(bp: AgentBlueprint): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete blueprint "${bp.name}"? Agents already spawned from it are untouched.`,
        confirmLabel: "Delete",
        variant: "danger",
      }))
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.agents.deleteBlueprint(bp.id);
      setBlueprints((prev) => prev.filter((b) => b.id !== bp.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <LayoutTemplate size={14} />
          Blueprints
        </div>
        <button
          type="button"
          onClick={() => setCreating((v) => !v)}
          className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] hover:bg-surface"
        >
          <Plus size={12} /> New blueprint
        </button>
      </div>
      <p className="text-[11px] text-muted">
        Saved task + governance profiles. Instantiate one to spawn a fresh agent with the same
        instructions, model, budget and retry settings.
      </p>

      {creating && (
        <BlueprintForm
          onCancel={() => setCreating(false)}
          onCreated={(bp) => {
            setBlueprints((prev) => [...prev, bp]);
            setCreating(false);
          }}
        />
      )}

      {error && <p className="text-[11px] text-red-500">{error}</p>}

      {blueprints.length === 0 ? (
        <p className="text-[11px] text-muted">No blueprints yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {blueprints.map((bp) => (
            <li
              key={bp.id}
              className="flex items-center gap-2 rounded border border-border bg-bg px-2.5 py-1.5"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium">{bp.name}</div>
                {bp.description && (
                  <div className="truncate text-[11px] text-muted">{bp.description}</div>
                )}
              </div>
              {bp.token_budget != null && (
                <span
                  className="shrink-0 rounded bg-border/60 px-1 text-[10px] text-muted"
                  title="Token budget"
                >
                  {bp.token_budget.toLocaleString()} tok
                </span>
              )}
              <button
                type="button"
                onClick={() => void instantiate(bp)}
                disabled={busy}
                className="flex shrink-0 items-center gap-1 rounded border border-border px-2 py-1 text-[11px] hover:bg-surface disabled:opacity-50"
                data-tooltip="Spawn an agent from this blueprint"
              >
                <Play size={11} /> Run
              </button>
              <button
                type="button"
                onClick={() => void remove(bp)}
                disabled={busy}
                className="shrink-0 rounded p-1 text-muted hover:text-red-500 disabled:opacity-50"
                aria-label={`Delete blueprint ${bp.name}`}
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function BlueprintForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (bp: AgentBlueprint) => void;
}) {
  const [name, setName] = useState("");
  const [task, setTask] = useState("");
  const [policy, setPolicy] = useState<AgentApprovalPolicy | "">("");
  const [tokenBudget, setTokenBudget] = useState("");
  const [maxRetries, setMaxRetries] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    setError(null);
    try {
      const budget = tokenBudget.trim() === "" ? null : Math.max(0, Math.floor(Number(tokenBudget)));
      const bp = await api.agents.createBlueprint({
        name: trimmed,
        task_prompt: task.trim() || undefined,
        approval_policy: policy || null,
        token_budget: Number.isNaN(budget as number) ? null : budget,
        max_retries: maxRetries,
      });
      onCreated(bp);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-2.5 rounded border border-border bg-surface/40 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">New blueprint</span>
        <button
          type="button"
          onClick={onCancel}
          className="rounded p-0.5 text-muted hover:bg-surface"
          aria-label="Cancel"
        >
          <X size={14} />
        </button>
      </div>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Blueprint name"
        className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
      />
      <textarea
        value={task}
        onChange={(e) => setTask(e.target.value)}
        placeholder="Default instructions (task prompt)…"
        rows={4}
        className="w-full resize-y rounded border border-border bg-bg px-2 py-1.5 font-mono text-xs leading-snug outline-none focus:border-accent"
      />
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="mb-1 block text-[11px] text-muted">Token budget</label>
          <input
            type="number"
            min={0}
            step={1000}
            value={tokenBudget}
            onChange={(e) => setTokenBudget(e.target.value)}
            placeholder="Unlimited"
            className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
        </div>
        <div>
          <label className="mb-1 block text-[11px] text-muted">Max retries</label>
          <input
            type="number"
            min={0}
            max={10}
            value={maxRetries}
            onChange={(e) => setMaxRetries(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
            className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>
      <div>
        <label className="mb-1 block text-[11px] text-muted">Approval policy</label>
        <Select
          fullWidth
          size="sm"
          ariaLabel="Approval policy for this blueprint"
          value={policy}
          onChange={(v) => setPolicy(v as AgentApprovalPolicy | "")}
          options={[
            { value: "", label: "Inherit global default" },
            ...APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label })),
          ]}
        />
      </div>
      {error && <p className="text-[11px] text-red-500">{error}</p>}
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded border border-border px-2.5 py-1 text-xs hover:bg-surface"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={!name.trim() || saving}
          className="flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-50"
        >
          {saving && <Loader2 size={12} className="animate-spin" />}
          Create
        </button>
      </div>
    </div>
  );
}
