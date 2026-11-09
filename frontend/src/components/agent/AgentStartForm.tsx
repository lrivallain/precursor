import { useMemo } from "react";
import { Bot, PauseCircle, Radar, ShieldCheck } from "lucide-react";
import type { SlashCommand } from "../../lib/commands";
import { useSettings } from "../../lib/settingsStore";
import type { AgentComposerInfra, AgentStartForm as FormState } from "../../lib/useAgentStartForm";
import type { AgentApprovalPolicy, Collection, Topic } from "../../lib/types";
import { APPROVAL_POLICIES } from "../AgentsSettings";
import { Composer } from "../Composer";
import { ComposerModelControls } from "../ComposerModelControls";
import { RoleSelector } from "../RoleSelector";
import { Select } from "../Select";
import { TopicPicker } from "../TopicPicker";

// Agents support only the system-handled slash commands, which apply to an
// existing session: the start composer offers none.
const NO_SUGGESTIONS: SlashCommand[] = [];
const NO_HISTORY: string[] = [];
const NOOP = (): void => {};

// Autonomy opt-in: turn the one-shot task into a background mission.
function AutonomyToggle({ form, disabled }: { form: FormState; disabled: boolean }) {
  return (
    <div className="rounded-lg border border-border bg-surface/50 p-2.5">
      <label className="flex items-start gap-2.5">
        <button
          type="button"
          role="switch"
          aria-checked={form.autonomy}
          disabled={disabled}
          onClick={form.toggleAutonomy}
          className={`mt-0.5 flex h-4 w-7 shrink-0 items-center rounded-full p-0.5 transition disabled:opacity-50 ${
            form.autonomy ? "bg-violet-500" : "bg-border"
          }`}
        >
          <span
            className={`h-3 w-3 rounded-full bg-white shadow-sm transition-transform ${form.autonomy ? "translate-x-3" : ""}`}
          />
        </button>
        <span className="min-w-0">
          <span className="flex items-center gap-1.5 text-[12px] font-medium">
            <Radar size={13} className="text-violet-500" />
            Run autonomously
          </span>
          <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">
            Pursues the objective on its own, continuing between turns and
            pausing only when it finishes or needs your input.
          </span>
        </span>
      </label>
      {form.autonomy && (
        <label className="mt-2.5 flex items-center gap-2 pl-[38px] text-[11px] text-muted">
          Step budget
          <input
            type="number"
            min={1}
            max={100}
            value={form.maxSteps}
            disabled={disabled}
            onChange={(e) => form.setMaxSteps(Math.min(100, Math.max(1, Number(e.target.value) || 1)))}
            className="w-16 rounded border border-border bg-bg px-1.5 py-0.5 text-center tabular-nums outline-none focus:border-accent"
          />
          <span className="text-[10px]">continuations before it hands back</span>
        </label>
      )}
    </div>
  );
}

function ApprovalPolicyPicker({ form, disabled }: { form: FormState; disabled: boolean }) {
  const settings = useSettings();
  // Short label of the global approval default, shown on the per-agent
  // "Inherit" option so the fallback is legible without opening Settings.
  const globalPolicyLabel = useMemo(() => {
    const found = APPROVAL_POLICIES.find((p) => p.value === settings?.agents_approval_policy);
    return found ? found.label.split("—")[0].trim() : "";
  }, [settings?.agents_approval_policy]);
  const hint = form.approvalPolicy
    ? (APPROVAL_POLICIES.find((p) => p.value === form.approvalPolicy)?.hint ?? "")
    : "Falls back to the global default set in Settings; takes effect on the agent's next turn.";
  return (
    <div className="rounded-lg border border-border bg-surface/50 p-2.5">
      <label className="flex items-center gap-1.5 text-[12px] font-medium">
        <ShieldCheck size={13} className="text-violet-500" />
        Approval policy
      </label>
      <div className="mt-2">
        <Select
          fullWidth
          size="sm"
          ariaLabel="Approval policy for this agent"
          disabled={disabled}
          value={form.approvalPolicy}
          onChange={(v) => form.setApprovalPolicy(v as AgentApprovalPolicy | "")}
          options={[
            { value: "", label: `Inherit global default${globalPolicyLabel ? ` — ${globalPolicyLabel}` : ""}` },
            ...APPROVAL_POLICIES.map((p) => ({ value: p.value, label: p.label })),
          ]}
        />
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted">{hint}</p>
    </div>
  );
}

function ParkedToggle({ form, disabled }: { form: FormState; disabled: boolean }) {
  return (
    <label
      className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-[12px] transition ${
        form.start ? "border-border bg-surface/50" : "border-violet-500/50 bg-violet-500/10"
      }`}
    >
      <input
        type="checkbox"
        className="mt-0.5 accent-violet-500"
        checked={!form.start}
        disabled={disabled}
        onChange={form.toggleStart}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 font-medium">
          <PauseCircle size={13} className="text-violet-500" />
          Create parked (don't run yet)
        </span>
        <span className="mt-0.5 block text-[11px] leading-relaxed text-muted">
          Arm the agent in the <span className="font-medium">waiting</span> state
          and start it later — on a parent completing, a webhook, or a manual
          “Start now”.
        </span>
      </span>
    </label>
  );
}

/** Enabled but nothing selected: the "start a new task" surface. */
export function AgentStartForm({
  form,
  composer,
  topics,
  collections,
  disabled,
  error,
  roleOpen,
  onRoleOpenChange,
  onSubmit,
}: {
  form: FormState;
  composer: AgentComposerInfra;
  topics: Topic[];
  collections: Collection[];
  /** The runtime isn't available or a create is in flight. */
  disabled: boolean;
  error: string | null;
  roleOpen: boolean;
  onRoleOpenChange: (open: boolean) => void;
  onSubmit: () => void;
}) {
  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-3 p-8">
      <div className="flex items-center gap-2">
        <Bot size={18} />
        <h2 className="text-sm font-medium">Start an agent task</h2>
      </div>
      <p className="text-[12px] text-muted">
        Describe a task to hand off. The agent runs on its own and posts results
        back to a topic or chat when you attach one.
      </p>
      <label className="flex items-center gap-2 text-[12px] text-muted">
        Associate with topic
        <TopicPicker
          topics={topics}
          value={form.topicId}
          onChange={form.setTopicId}
          disabled={disabled}
          collections={collections}
        />
      </label>
      <AutonomyToggle form={form} disabled={disabled} />
      <ApprovalPolicyPicker form={form} disabled={disabled} />
      <ParkedToggle form={form} disabled={disabled} />
      {error && <p className="text-[11px] text-red-500">{error}</p>}
      <Composer
        value={form.task}
        onChange={form.setTask}
        onSend={onSubmit}
        onStop={NOOP}
        streaming={false}
        suggestions={NO_SUGGESTIONS}
        userHistory={NO_HISTORY}
        speech={composer.speech}
        interimText={composer.interimText}
        height={composer.height}
        onResizeStart={composer.onResizeStart}
        autoFocus
        placeholder="e.g. Investigate the flaky CI test and propose a fix…"
        toolbarStart={
          <>
            <ComposerModelControls variant="agents" />
            <RoleSelector
              value={form.role}
              onChange={form.setRole}
              open={roleOpen}
              onOpenChange={onRoleOpenChange}
              disabled={disabled}
            />
          </>
        }
      />
    </div>
  );
}
