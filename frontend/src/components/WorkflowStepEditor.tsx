import { useEffect, type ReactNode } from "react";
import { Bot, ShieldCheck, Sparkles, UserCheck, X } from "lucide-react";
import type {
  AgentModelInfo,
  AgentSession,
  Workflow,
  WorkflowStepContextMode,
  WorkflowStepErrorPolicy,
  WorkflowStepInput,
  WorkflowStepKind,
  WorkflowStepRejectPolicy,
} from "../lib/types";

/**
 * The draft model behind step authoring, plus the editor for a *single* step.
 *
 * Ordering and insertion happen on the workflow board itself — the horizontal
 * strip stays the one canonical picture of the pipeline — so this modal only has
 * to own one step's settings: its kind, the agent behind it, its instructions,
 * what it's fed, what it may use, and what happens when it fails or is rejected.
 *
 * Everything here is *controlled*: it owns no persistence. The board holds the
 * drafts and decides when to save, because saving replaces the whole step list
 * server-side — so a keystroke-level autosave would churn every row's id.
 */

export interface DraftStep {
  key: string;
  mode: "existing" | "inline";
  agentId: number | null;
  name: string;
  task: string;
  title: string;
  model: string;
  kind: WorkflowStepKind;
  /** 1-based step number to retry on gate FAIL / approval reject; empty = previous. */
  onFail: string;
  /** Extra mandate for this step only, layered on the agent's objective. */
  instructions: string;
  /** What to do when this step fails or stalls. */
  onError: WorkflowStepErrorPolicy;
  /** Retry budget when onError is "retry". */
  maxRetries: number;
  /** Approval steps: what a rejection does next. */
  onReject: WorkflowStepRejectPolicy;
  /** What this step is fed. */
  contextMode: WorkflowStepContextMode;
  /** For "selected": comma-separated 1-based step numbers shown to the user. */
  contextSources: string;
  /** Capability overrides; null = inherit the agent's own setting. */
  useMcp: boolean | null;
  useSkills: boolean | null;
  useMemory: boolean | null;
}

let draftSeq = 0;

export function newDraft(mode: DraftStep["mode"] = "inline"): DraftStep {
  draftSeq += 1;
  return {
    key: `d${draftSeq}`,
    mode,
    agentId: null,
    name: "",
    task: "",
    title: "",
    model: "",
    kind: "task",
    onFail: "",
    instructions: "",
    onError: "fail",
    maxRetries: 1,
    onReject: "rework",
    contextMode: "auto",
    contextSources: "",
    useMcp: null,
    useSkills: null,
    useMemory: null,
  };
}

/** Hydrate editable drafts from a saved workflow (or one blank step for a new one). */
export function draftsFromWorkflow(workflow: Workflow | null): DraftStep[] {
  if (!workflow || workflow.steps.length === 0) return [newDraft()];
  return workflow.steps.map((s) => ({
    key: `s${s.id}`,
    // The agent's own `inline` flag is what says the prompt was authored in this
    // step (true for an inline task *and* an inline gate), so it — not the
    // step's kind — decides which mode the editor reopens in.
    mode:
      s.kind === "inline" || s.agent?.inline || s.agent_id == null
        ? ("inline" as const)
        : ("existing" as const),
    agentId: s.agent_id,
    name: s.name ?? "",
    // A step-authored prompt round-trips through its hidden vessel's objective.
    task: s.kind === "inline" || s.agent?.inline ? (s.agent?.task_prompt ?? "") : "",
    // Not user-editable: a hidden vessel is named after its step, so the server
    // derives the title. Kept on the draft only for a referenced agent, whose
    // real title we must not overwrite.
    title: s.agent?.inline ? "" : (s.agent?.title ?? ""),
    model: "",
    kind: s.kind ?? "task",
    onFail: s.on_fail_position != null ? String(s.on_fail_position + 1) : "",
    instructions: s.instructions ?? "",
    onError: s.on_error ?? "fail",
    maxRetries: s.max_retries || 1,
    onReject: s.on_reject ?? "rework",
    contextMode: s.context_mode ?? "auto",
    // Stored 0-based, shown 1-based to match the "Step N" labels.
    contextSources: (s.context_sources ?? "")
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean)
      .map((p) => String(Number(p) + 1))
      .join(","),
    useMcp: s.use_mcp,
    useSkills: s.use_skills,
    useMemory: s.use_memory,
  }));
}

/**
 * Convert drafts to the API payload, or null when a step is incomplete (no agent
 * chosen, or an inline step with no task) — the caller surfaces that as an error
 * rather than silently saving a broken pipeline.
 */
export function draftsToPayload(steps: DraftStep[]): WorkflowStepInput[] | null {
    const out: WorkflowStepInput[] = [];
    for (const [idx, s] of steps.entries()) {
      // A gate's on-fail target: parse the 1-based field to a 0-based position;
      // blank or invalid falls back to null (backend uses the previous step).
      let onFail: number | null = null;
      if ((s.kind === "gate" || s.kind === "approval") && s.onFail.trim()) {
        const n = Number(s.onFail.trim());
        if (Number.isFinite(n) && n >= 1) onFail = n - 1;
      }
      // Shared across both modes: the step's own mandate + failure handling.
      // Back to 0-based positions for the API, dropping anything unparseable
      // or self-referential.
      const sources = s.contextSources
        .split(",")
        .map((p) => Number(p.trim()))
        .filter((n) => Number.isFinite(n) && n >= 1)
        .map((n) => n - 1)
        .filter((n) => n !== idx);
      const policy = {
        name: s.name.trim() || null,
        kind: s.kind,
        on_fail_position: onFail,
        instructions: s.instructions.trim() || null,
        on_error: s.onError,
        max_retries: s.onError === "retry" ? Math.max(1, s.maxRetries) : 0,
        context_mode: s.contextMode,
        context_sources: s.contextMode === "selected" ? sources.join(",") || null : null,
        use_mcp: s.useMcp,
        use_skills: s.useSkills,
        use_memory: s.useMemory,
      };
      if (s.kind === "approval") {
        // A human checkpoint runs no agent — send it bare, with its own policy.
        out.push({ ...policy, on_error: "fail", max_retries: 0, on_reject: s.onReject });
      } else if (s.kind !== "inline" && s.mode === "existing") {
        // Reuses an agent from the Agents section.
        if (s.agentId == null) return null;
        out.push({ agent_id: s.agentId, ...policy });
      } else {
        // Authored here — a task or a gate. Sending `task` is what tells the
        // server this agent is private to the step; passing the known agent_id
        // alongside makes it update that vessel instead of minting a new one,
        // so the step keeps its run history across saves.
        if (!s.task.trim()) return null;
        out.push({
          agent_id: s.agentId ?? null,
          task: s.task.trim(),
          // The vessel is named after the step, so there's nothing to type.
          title: s.name.trim() || null,
          model: s.model || null,
          ...policy,
        });
      }
    }
    return out;
}


interface Props {
  step: DraftStep;
  /** 0-based position, for the title and the "previous step" placeholders. */
  index: number;
  agents: AgentSession[];
  models: AgentModelInfo[];
  /** Agent ids already used by other steps, so the picker can flag reuse. */
  usedAgentIds: Set<number | null>;
  onChange: (next: Partial<DraftStep>) => void;
  onClose: () => void;
}

const KIND_TITLE: Record<WorkflowStepKind, string> = {
  task: "Agent step",
  inline: "Inline step",
  gate: "Gate step",
  approval: "Approval step",
};

/** One labelled group of the step form. Keeps every kind in the same order. */
function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</h3>
        <span className="text-[11px] text-muted/70">{hint}</span>
      </div>
      {children}
    </section>
  );
}

const TONE_ACTIVE: Record<string, string> = {
  indigo: "bg-indigo-500/15 text-indigo-500",
  sky: "bg-sky-500/15 text-sky-500",
  amber: "bg-amber-500/15 text-amber-500",
  violet: "bg-violet-500/15 text-violet-500",
};

/** A segmented-control button. Used for every choice in this form so the
 *  selectors all read identically regardless of what they're choosing. */
function KindButton({
  active,
  onClick,
  label,
  icon,
  tone = "indigo",
  title,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  icon?: ReactNode;
  tone?: keyof typeof TONE_ACTIVE;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={`flex items-center gap-1 rounded px-2 py-0.5 text-[11px] transition ${
        active ? TONE_ACTIVE[tone] : "text-muted hover:text-fg"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

/** Edit one step of a workflow. Reorder/add/remove live on the board. */
export function WorkflowStepEditModal({
  step,
  index,
  agents,
  models,
  usedAgentIds,
  onChange,
  onClose,
}: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const patch = (next: Partial<DraftStep>) => onChange(next);
  const isInline = step.kind === "inline";
  // The step's own prompt already lives in the field above whenever the agent is
  // authored here (inline, or a brand-new agent), so the customisation box only
  // appears for a step that reuses an agent defined in the Agents section.
  const showInstructions = !isInline && step.kind !== "approval" && step.mode === "existing";
  // An approval runs nothing, so it has neither an agent picker nor authoring
  // fields; an inline step always authors its own prompt.
  const agentBacked = step.kind === "task" || step.kind === "gate";
  const authorsAgent = isInline || (agentBacked && step.mode === "inline");
  const defaultLabel =
    step.kind === "approval"
      ? "Human approval"
      : step.kind === "gate"
        ? "Quality gate"
        : `Step ${index + 1}`;
  const idx = index;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-500/15 text-[11px] font-semibold text-indigo-500">
            {index + 1}
          </span>
          <h2 className="text-sm font-semibold text-fg">{KIND_TITLE[step.kind]}</h2>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-lg p-1.5 text-muted transition hover:bg-white/5 hover:text-fg"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Every step kind reads top-to-bottom in the same order: what it is,
            what it's called, what it does, what it works with, how it ends.
            Sections that don't apply to a kind are simply absent, so switching
            kinds never reshuffles the fields that remain. */}
        <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
          {/* 0 — What kind of step this is. */}
          <Section title="Step type" hint="what this step does in the pipeline">
            <div className="inline-flex rounded-lg border border-border p-0.5">
              <KindButton
                active={step.kind === "task"}
                onClick={() => patch({ kind: "task", mode: "existing" })}
                tone="indigo"
                label="Agent"
                title="Runs a reusable agent from the Agents section"
              />
              <KindButton
                active={step.kind === "inline"}
                onClick={() => patch({ kind: "inline", mode: "inline" })}
                tone="sky"
                icon={<Sparkles size={12} />}
                label="Inline"
                title="A one-off task written here — no reusable agent is created"
              />
              <KindButton
                active={step.kind === "gate"}
                onClick={() => patch({ kind: "gate" })}
                tone="amber"
                icon={<ShieldCheck size={12} />}
                label="Gate"
                title="An agent votes PASS/FAIL on the work so far"
              />
              <KindButton
                active={step.kind === "approval"}
                onClick={() => patch({ kind: "approval" })}
                tone="violet"
                icon={<UserCheck size={12} />}
                label="Approval"
                title="Pause here for a human decision — no agent runs"
              />
            </div>

            {step.kind === "inline" && (
              <p className="rounded-lg border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-[11px] leading-relaxed text-sky-600/90 dark:text-sky-400/80">
                A one-off step. Its instructions live here rather than in a reusable agent, so
                nothing is added to your Agents list — and it is removed along with the step.
              </p>
            )}
            {step.kind === "gate" && step.mode === "inline" && (
              <p className="rounded-lg border border-sky-500/25 bg-sky-500/5 px-3 py-2 text-[11px] leading-relaxed text-sky-600/90 dark:text-sky-400/80">
                A one-off gate: its check is written here rather than in a reusable agent, so
                nothing is added to your Agents list — and it is removed along with the step.
              </p>
            )}
            {step.kind === "gate" && (
              <p className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[11px] leading-relaxed text-amber-600/90 dark:text-amber-400/80">
                A gate reviews the previous step and ends its turn with{" "}
                <code className="rounded bg-black/10 px-1 dark:bg-white/10">PASS</code> or{" "}
                <code className="rounded bg-black/10 px-1 dark:bg-white/10">FAIL</code>. On FAIL
                the workflow loops back and re-runs an earlier step.
              </p>
            )}
            {step.kind === "approval" && (
              <p className="rounded-lg border border-violet-500/20 bg-violet-500/5 px-3 py-2 text-[11px] leading-relaxed text-violet-600/90 dark:text-violet-400/80">
                The run <strong>parks here</strong> until you approve it — no agent runs and
                nothing is spent while it waits. Put a checkpoint before anything irreversible
                (sending an email, publishing, paying).
              </p>
            )}
          </Section>

          {/* 1 — What it's called on the board. */}
          <Section title="Step label" hint="shown on the board and in the run trace">
            <input
              value={step.name}
              onChange={(e) => patch({ name: e.target.value.slice(0, 200) })}
              placeholder={defaultLabel}
              className="w-full rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm text-fg outline-none transition placeholder:text-muted/70 focus:border-indigo-500"
            />
          </Section>

          {/* 2 — What it should actually do. */}
          <Section
            title={step.kind === "approval" ? "What to review" : "What this step does"}
            hint={
              step.kind === "approval"
                ? "shown on the checkpoint when the run parks here"
                : showInstructions
                  ? "how this step customises the agent"
                  : authorsAgent
                    ? "written here, used only by this step"
                    : "its objective"
            }
          >
            {/* Only an agent-backed step chooses between reusing an agent and
                authoring one; inline always authors, approval runs nothing. */}
            {(step.kind === "task" || step.kind === "gate") && (
              <div className="inline-flex rounded-lg border border-border p-0.5">
                <KindButton
                  active={step.mode === "existing"}
                  onClick={() => patch({ mode: "existing" })}
                  tone="indigo"
                  icon={<Bot size={12} />}
                  label="Existing agent"
                  title="Reuse an agent from the Agents section"
                />
                <KindButton
                  active={step.mode === "inline"}
                  onClick={() => patch({ mode: "inline" })}
                  tone="sky"
                  icon={<Sparkles size={12} />}
                  label="Inline prompt"
                  title="Written here and used only by this step — no reusable agent is created"
                />
              </div>
            )}

            {agentBacked && step.mode === "existing" && (
              <select
                value={step.agentId ?? ""}
                onChange={(e) => patch({ agentId: e.target.value ? Number(e.target.value) : null })}
                className="w-full rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              >
                <option value="">Pick an agent…</option>
                {agents.map((a) => (
                  <option
                    key={a.id}
                    value={a.id}
                    disabled={usedAgentIds.has(a.id) && a.id !== step.agentId}
                  >
                    {a.title}
                  </option>
                ))}
              </select>
            )}

            {authorsAgent && (
              <div className="space-y-2">
                <textarea
                  value={step.task}
                  onChange={(e) => patch({ task: e.target.value })}
                  rows={isInline ? 4 : 3}
                  placeholder={
                    isInline
                      ? "What should this step do? Written here, used only by this step."
                      : step.kind === "gate"
                        ? "What exactly should this gate check for?"
                        : "Objective for this step's agent…"
                  }
                  className="w-full resize-y rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm outline-none focus:border-indigo-500"
                />
                <label className="flex items-center gap-2 text-[11px] text-muted">
                  Model
                  <select
                    value={step.model}
                    onChange={(e) => patch({ model: e.target.value })}
                    className="rounded-lg border border-border bg-bg/40 px-2 py-1.5 text-xs text-fg outline-none focus:border-indigo-500"
                  >
                    <option value="">Default</option>
                    {models.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            )}

            {/* Exactly one prose field per step: a customisation box only where
                it adds to a prompt written elsewhere, and the reviewer brief on
                an approval, which has no other description. */}
            {(showInstructions || step.kind === "approval") && (
              <textarea
                value={step.instructions}
                onChange={(e) => patch({ instructions: e.target.value.slice(0, 8000) })}
                rows={3}
                placeholder={
                  step.kind === "approval"
                    ? "What should the reviewer check before approving?"
                    : "Layered on top of the agent's own objective, for this step only."
                }
                className="w-full resize-y rounded-lg border border-border bg-bg/40 px-3 py-2 text-sm text-fg outline-none transition placeholder:text-muted/70 focus:border-indigo-500"
              />
            )}
          </Section>

          {/* 3 — What it works with. Meaningless for a step that runs nothing. */}
          {step.kind !== "approval" && (
            <Section title="Context & tools" hint="what it inherits and what it may use">
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted">Context</span>
                <div className="inline-flex rounded-lg border border-border p-0.5">
                  {(
                    [
                      ["auto", "Previous step"],
                      ["selected", "Pick steps"],
                      ["none", "None"],
                    ] as [WorkflowStepContextMode, string][]
                  ).map(([value, label]) => (
                    <KindButton
                      key={value}
                      active={step.contextMode === value}
                      onClick={() => patch({ contextMode: value })}
                      tone="indigo"
                      label={label}
                    />
                  ))}
                </div>
                {step.contextMode === "selected" && (
                  <label className="flex items-center gap-1.5 text-muted">
                    from step
                    <input
                      value={step.contextSources}
                      onChange={(e) =>
                        patch({ contextSources: e.target.value.replace(/[^0-9,]/g, "") })
                      }
                      placeholder="1,3"
                      className="w-20 rounded border border-border bg-bg/40 px-2 py-1 text-center text-xs text-fg outline-none focus:border-indigo-500"
                    />
                  </label>
                )}
                {step.contextMode === "none" && (
                  <span className="text-muted/70">runs on its own objective only</span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted">Can use</span>
                {(
                  [
                    ["useMcp", "Tools"],
                    ["useSkills", "Skills"],
                    ["useMemory", "Memory"],
                  ] as ["useMcp" | "useSkills" | "useMemory", string][]
                ).map(([field, label]) => {
                  const value = step[field];
                  const next = value === null ? true : value ? false : null;
                  return (
                    <button
                      key={field}
                      type="button"
                      onClick={() => patch({ [field]: next } as Partial<DraftStep>)}
                      title="Click to cycle: inherit → on → off"
                      className={`rounded-lg border px-2 py-0.5 transition ${
                        value === null
                          ? "border-border text-muted hover:text-fg"
                          : value
                            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-500"
                            : "border-red-500/40 bg-red-500/10 text-red-500"
                      }`}
                    >
                      {label}
                      <span className="ml-1 opacity-70">
                        {value === null ? "auto" : value ? "on" : "off"}
                      </span>
                    </button>
                  );
                })}
              </div>
            </Section>
          )}

          {/* 4 — How it ends: a failure for anything that runs, a rejection for
              a gate's loop-back target and a human checkpoint. */}
          <Section title="Ending policies" hint="what happens when it fails or is rejected">
            {step.kind !== "approval" && (
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted">If this step fails</span>
                <div className="inline-flex rounded-lg border border-border p-0.5">
                  {(["fail", "retry", "continue"] as WorkflowStepErrorPolicy[]).map((p) => (
                    <KindButton
                      key={p}
                      active={step.onError === p}
                      onClick={() => patch({ onError: p })}
                      tone="indigo"
                      label={p === "fail" ? "Stop run" : p === "retry" ? "Retry" : "Carry on"}
                    />
                  ))}
                </div>
                {step.onError === "retry" && (
                  <label className="flex items-center gap-1.5 text-muted">
                    up to
                    <input
                      type="number"
                      min={1}
                      max={10}
                      value={step.maxRetries}
                      onChange={(e) =>
                        patch({
                          maxRetries: Math.min(10, Math.max(1, Number(e.target.value) || 1)),
                        })
                      }
                      className="w-14 rounded border border-border bg-bg/40 px-2 py-1 text-center text-xs text-fg outline-none focus:border-indigo-500"
                    />
                    times
                  </label>
                )}
              </div>
            )}

            {step.kind === "approval" && (
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-muted">If you reject</span>
                <div className="inline-flex rounded-lg border border-border p-0.5">
                  {(
                    [
                      ["rework", "Send back"],
                      ["stop", "Stop the run"],
                      ["skip", "Skip ahead"],
                    ] as [WorkflowStepRejectPolicy, string][]
                  ).map(([value, label]) => (
                    <KindButton
                      key={value}
                      active={step.onReject === value}
                      onClick={() => patch({ onReject: value })}
                      tone="violet"
                      label={label}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Where a rejection (human) or FAIL verdict (gate) sends the run. */}
            {((step.kind === "approval" && step.onReject === "rework") ||
              step.kind === "gate") && (
              <label className="flex items-center gap-2 text-[11px] text-muted">
                {step.kind === "gate" ? "On fail, retry step" : "If sent back, redo step"}
                <input
                  value={step.onFail}
                  onChange={(e) => patch({ onFail: e.target.value.replace(/[^0-9]/g, "") })}
                  placeholder={String(idx)}
                  className="w-14 rounded border border-border bg-bg/40 px-2 py-1 text-center text-xs text-fg outline-none focus:border-indigo-500"
                />
                <span className="text-muted/70">(blank = previous step)</span>
              </label>
            )}
            {step.kind === "approval" && step.onReject === "stop" && (
              <p className="text-[11px] text-muted">
                Rejecting ends the run here — for checkpoints in front of something irreversible.
              </p>
            )}
            {step.kind === "approval" && step.onReject === "skip" && (
              <p className="text-[11px] text-muted">
                Rejecting drops this work and continues with the following step.
              </p>
            )}
          </Section>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <span className="mr-auto text-[11px] text-muted">
            Changes apply when you save the workflow&apos;s steps.
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
