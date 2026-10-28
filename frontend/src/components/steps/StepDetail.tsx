import { useState } from "react";
import { MessageSquare, FileText, Scissors } from "lucide-react";
import { api } from "../../lib/api";
import {
  addChildBlocker,
  isGroup,
  progressOf,
  statusActions,
  type StatusAction,
  type StepNode,
} from "../../lib/stepPlans";
import { useConfirm } from "../ConfirmDialog";
import { PlanChat } from "./PlanChat";
import { StepArtifactEditor } from "./StepArtifactEditor";
import { StepEditor } from "./StepEditor";
import { BTN_PRIMARY, BTN_SECONDARY, GuidedAction, StepStatusPill } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

const PRIMARY_STATUS = new Set(["in_progress", "validated"]);

/** The selected step: edit it, move it through its statuses, and work its artifact or chat. */
export function StepDetail({
  node,
  mutate,
  reload,
  onSelect,
}: {
  node: StepNode;
  mutate: Mutate;
  reload: () => Promise<void>;
  onSelect: (id: number) => void;
}) {
  const { step } = node;
  const group = isGroup(node);
  const confirm = useConfirm();
  const [tab, setTab] = useState<"artifact" | "discussion">("artifact");
  const suggested = node.children.filter((c) => c.step.status === "proposed").length;

  async function setStatus(action: StatusAction): Promise<void> {
    if (action.status === "cancelled") {
      const ok = await confirm({
        title: group ? "Cancel this group?" : "Cancel this step?",
        message: group
          ? "The group and all its sub-steps will be cancelled. You can reopen them later."
          : "A cancelled step no longer counts toward the final output. You can reopen it later.",
        confirmLabel: "Cancel step",
        cancelLabel: "Keep",
        variant: "warning",
      });
      if (!ok) return;
    }
    await mutate(() => api.stepPlans.setStepStatus(step.id, action.status));
  }

  if (step.status === "proposed") {
    return (
      <div className="space-y-4">
        <StepHeading title={step.title} status={step.effective_status} />
        <p className="text-sm text-muted">
          This step is a suggestion from the model. Accept it to make it part of the plan, or
          dismiss it.
        </p>
        <StepEditor step={step} mutate={mutate} />
        <div className="flex gap-2">
          <button type="button" className={BTN_PRIMARY} onClick={() => void mutate(() => api.stepPlans.acceptStep(step.id))}>
            Accept
          </button>
          <button type="button" className={BTN_SECONDARY} onClick={() => void mutate(() => api.stepPlans.removeStep(step.id))}>
            Dismiss
          </button>
        </div>
      </div>
    );
  }

  const actions = statusActions(node);
  const splitBlocker = addChildBlocker(node);
  const { done, total } = progressOf(node);

  return (
    <div className="space-y-4">
      <StepHeading title={step.title} status={step.effective_status} />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Step status actions">
        {actions.map((action) => (
          <button
            key={action.status + action.label}
            type="button"
            disabled={action.disabledReason !== undefined}
            data-tooltip={action.disabledReason}
            onClick={() => void setStatus(action)}
            className={PRIMARY_STATUS.has(action.status) && !action.disabledReason ? BTN_PRIMARY : BTN_SECONDARY}
          >
            {action.label}
          </button>
        ))}
      </div>

      <StepEditor step={step} mutate={mutate} />

      {group ? (
        <section className="space-y-2 rounded-lg border border-border p-3" aria-label="Sub-steps">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted">
            Sub-steps · {done}/{total} validated
          </h4>
          <p className="text-xs text-muted">
            This step is a group: its status follows its sub-steps and the work happens in them.
          </p>
          <ul className="space-y-1">
            {node.children.map((c) => (
              <li key={c.step.id}>
                <button
                  type="button"
                  onClick={() => onSelect(c.step.id)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-surface"
                >
                  <span className="min-w-0 flex-1 truncate">{c.step.title}</span>
                  <StepStatusPill status={c.step.effective_status} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <>
          <div className="flex gap-1 border-b border-border" role="tablist" aria-label="Step work">
            {(
              [
                ["artifact", "Artifact", <FileText key="a" size={14} />],
                ["discussion", "Discussion", <MessageSquare key="d" size={14} />],
              ] as const
            ).map(([id, label, icon]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                onClick={() => setTab(id)}
                className={`-mb-px inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm ${
                  tab === id
                    ? "border-accent font-medium text-accent"
                    : "border-transparent text-muted hover:text-fg"
                }`}
              >
                {icon} {label}
              </button>
            ))}
          </div>
          {tab === "artifact" ? (
            <StepArtifactEditor step={step} mutate={mutate} />
          ) : (
            <div className="h-[34rem] max-h-[70vh] overflow-hidden rounded-lg border border-border">
              <PlanChat
                key={step.id}
                open={() => api.stepPlans.stepChat(step.id)}
                onOpened={() => void reload()}
              />
            </div>
          )}

          <section className="space-y-2 rounded-lg border border-dashed border-border p-3" aria-label="Split into sub-steps">
            <h4 className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted">
              <Scissors size={13} /> Split into sub-steps
            </h4>
            <p className="text-xs text-muted">
              {splitBlocker ??
                (suggested > 0
                  ? `${suggested} suggested sub-step${suggested === 1 ? " is" : "s are"} waiting in the tree: accept the ones you want.`
                  : "Too big for one artifact? The model suggests smaller steps; accept the ones you want and this step becomes a group.")}
            </p>
            <GuidedAction
              label="Suggest sub-steps"
              busyLabel="Thinking…"
              secondary
              disabled={splitBlocker !== null}
              disabledReason={splitBlocker ?? undefined}
              onRun={(guidance) => mutate(() => api.stepPlans.suggestSubsteps(step.id, guidance || undefined))}
            />
          </section>
        </>
      )}
    </div>
  );
}

function StepHeading({ title, status }: { title: string; status: Parameters<typeof StepStatusPill>[0]["status"] }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{title}</h2>
      <StepStatusPill status={status} />
    </div>
  );
}
