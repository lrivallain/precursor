import { useEffect, useState } from "react";
import { Loader2, ShieldCheck, X } from "lucide-react";
import { api } from "../lib/api";
import { EmojiPicker } from "./EmojiPicker";
import type { AgentApprovalPolicy, Role, Workflow } from "../lib/types";
import { APPROVAL_POLICIES } from "./AgentsSettings";

interface Props {
  /** Existing workflow to edit, or null to create a fresh one. */
  workflow: Workflow | null;
  onSaved: (workflow: Workflow) => void;
  onCancel: () => void;
}

/**
 * Create / edit a workflow: identity (name, description, icon), the ordered
 * step list (reference an existing agent or author one inline), and the
 * per-run artifact-reset toggle. Scheduling + webhooks live on the detail view.
 */
export function WorkflowBuilder({ workflow, onSaved, onCancel }: Props) {
  const editing = workflow != null;
  const [name, setName] = useState(workflow?.name ?? "");
  const [description, setDescription] = useState(workflow?.description ?? "");
  const [icon, setIcon] = useState<string | null>(workflow?.icon ?? null);
  const [clearArtifacts, setClearArtifacts] = useState(workflow?.clear_artifacts ?? true);
  const [maxLoops, setMaxLoops] = useState<number>(workflow?.max_loops ?? 3);
  // Stall watchdog, edited in minutes; 0 = off (the default).
  const [timeoutMin, setTimeoutMin] = useState<number>(
    workflow?.step_timeout_seconds ? Math.round(workflow.step_timeout_seconds / 60) : 0,
  );
  // Workflow-wide Assistant Role: one voice for the whole pipeline, applied to
  // each step's agent at launch rather than stamped onto the shared agent rows.
  const [roleId, setRoleId] = useState<number | null>(workflow?.role_id ?? null);
  const [roles, setRoles] = useState<Role[]>([]);
  // Workflow-wide tool-approval policy. What makes an *unattended* pipeline
  // actually unattended: a step that stops at a permission gate parks the whole
  // run until a human answers, which a scheduled or webhook-fired workflow has
  // nobody to do. Null keeps each agent's own setting.
  const [approvalPolicy, setApprovalPolicy] = useState<AgentApprovalPolicy | null>(
    workflow?.approval_policy ?? null,
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.roles.list().then(setRoles).catch(() => {});
  }, []);



  async function save(): Promise<void> {
    setError(null);
    if (!name.trim()) {
      setError("Give the workflow a name.");
      return;
    }
    setSaving(true);
    try {
      const saved = editing
        ? await api.workflows.update(workflow!.id, {
            name: name.trim(),
            description: description.trim() || null,
            icon,
            clear_artifacts: clearArtifacts,
            max_loops: maxLoops,
            step_timeout_seconds: timeoutMin > 0 ? timeoutMin * 60 : 0,
            role_id: roleId ?? 0,
            approval_policy: approvalPolicy,
          })
        : await api.workflows.create({
            name: name.trim(),
            description: description.trim() || null,
            icon,
            clear_artifacts: clearArtifacts,
            max_loops: maxLoops,
            step_timeout_seconds: timeoutMin > 0 ? timeoutMin * 60 : null,
            role_id: roleId,
            approval_policy: approvalPolicy,
          });
      onSaved(saved);
    } catch {
      setError("Failed to save the workflow.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-border px-5 py-3">
        <h1 className="text-lg font-semibold text-fg">
          {editing ? "Edit workflow" : "New workflow"}
        </h1>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
        >
          <X size={18} />
        </button>
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
        {/* Identity */}
        <section className="space-y-3">
          <div className="flex gap-3">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted">Icon</label>
              <EmojiPicker value={icon} onChange={setIcon} />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-[11px] font-medium text-muted">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Weekly release digest"
                className="w-full rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[11px] font-medium text-muted">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="What this pipeline produces, and when it should run."
              className="w-full resize-none rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm outline-none focus:border-indigo-500"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-fg/90">
            <input
              type="checkbox"
              checked={clearArtifacts}
              onChange={(e) => setClearArtifacts(e.target.checked)}
              className="accent-indigo-500"
            />
            Clear each step's artifacts at the start of every run
          </label>
          <label className="flex items-center gap-2 text-sm text-fg/90">
            <span className="inline-flex items-center gap-1.5">
              <ShieldCheck size={14} className="text-amber-500" />
              Max gate retries per run
            </span>
            <input
              type="number"
              min={1}
              max={25}
              value={maxLoops}
              onChange={(e) => {
                const n = Number(e.target.value);
                setMaxLoops(Number.isFinite(n) ? Math.min(25, Math.max(1, n)) : 3);
              }}
              className="w-16 rounded-lg border border-border bg-bg/40 px-2 py-1 text-center text-sm outline-none focus:border-amber-500"
            />
          </label>

          <label className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg">
              Stall watchdog
              <span className="ml-1 text-[11px] text-muted">
                stop a step stuck this long (0 = off)
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              <input
                type="number"
                min={0}
                max={1440}
                value={timeoutMin}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setTimeoutMin(Number.isFinite(n) ? Math.min(1440, Math.max(0, n)) : 0);
                }}
                className="w-16 rounded-lg border border-border bg-bg/40 px-2 py-1 text-center text-sm outline-none focus:border-indigo-500"
              />
              <span className="text-[11px] text-muted">min</span>
            </span>
          </label>

          <label className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg">
              Assistant role
              <span className="ml-1 text-[11px] text-muted">
                one voice for every step in this workflow
              </span>
            </span>
            <select
              value={roleId ?? ""}
              onChange={(e) => setRoleId(e.target.value ? Number(e.target.value) : null)}
              className="max-w-[14rem] rounded-lg border border-border bg-bg/40 px-2 py-1 text-sm outline-none focus:border-indigo-500"
            >
              <option value="">Each agent&apos;s own role</option>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg">
              Tool approvals
              <span className="ml-1 text-[11px] text-muted">
                a gate nobody answers stalls the whole run
              </span>
            </span>
            <select
              value={approvalPolicy ?? ""}
              onChange={(e) =>
                setApprovalPolicy((e.target.value || null) as AgentApprovalPolicy | null)
              }
              className="max-w-[14rem] rounded-lg border border-border bg-bg/40 px-2 py-1 text-sm outline-none focus:border-indigo-500"
            >
              <option value="">Each agent&apos;s own policy</option>
              {APPROVAL_POLICIES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </section>

        {error && (
          <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-500">
            {error}
          </div>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-2 text-sm text-muted transition hover:text-fg"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-600 disabled:opacity-40"
        >
          {saving && <Loader2 size={14} className="animate-spin" />}
          {editing ? "Save changes" : "Create workflow"}
        </button>
      </div>
    </div>
  );
}
