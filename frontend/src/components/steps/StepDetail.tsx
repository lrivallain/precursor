import { useState } from "react";
import { MessageSquare, FileText, Loader2, Scissors } from "lucide-react";
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
import { BTN_PRIMARY, BTN_SECONDARY, INPUT, StepStatusPill } from "./StepUi";

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

  const chatting = !group && tab === "discussion";

  return (
    <div className={chatting ? "flex h-full flex-col gap-4" : "space-y-4"}>
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
        {!group && (
          <SplitButton
            blocker={splitBlocker}
            suggested={suggested}
            onRun={(guidance) => mutate(() => api.stepPlans.suggestSubsteps(step.id, guidance || undefined))}
          />
        )}
      </div>

      {chatting ? (
        <details className="rounded-lg border border-border px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-muted">Title and instruction</summary>
          <div className="pt-2">
            <StepEditor step={step} mutate={mutate} compact />
          </div>
        </details>
      ) : (
        <StepEditor step={step} mutate={mutate} />
      )}

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
            <div className="min-h-[28rem] flex-1 overflow-hidden rounded-lg border border-border">
              <PlanChat
                key={step.id}
                open={() => api.stepPlans.stepChat(step.id)}
                onOpened={() => void reload()}
              />
            </div>
          )}
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

/** "Split" in the action row: a popover with the optional steer, so it takes no room in the page. */
function SplitButton({
  blocker,
  suggested,
  onRun,
}: {
  blocker: string | null;
  suggested: number;
  onRun: (guidance: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState(false);

  async function run(): Promise<void> {
    setBusy(true);
    try {
      await onRun(guidance.trim());
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative ml-auto">
      <button
        type="button"
        disabled={blocker !== null}
        data-tooltip={blocker ?? "Too big for one artifact? Ask the model for smaller steps."}
        aria-label="Split into sub-steps"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={BTN_SECONDARY}
      >
        <Scissors size={14} /> Split
        {suggested > 0 && (
          <span className="rounded-full bg-accent/15 px-1.5 text-xs text-accent">{suggested}</span>
        )}
      </button>
      {open && blocker === null && (
        <div
          role="dialog"
          aria-label="Split into sub-steps"
          className="absolute right-0 z-20 mt-2 w-80 space-y-2 rounded-lg border border-border bg-bg p-3 shadow-lg"
        >
          <p className="text-xs text-muted">
            {suggested > 0
              ? `${suggested} suggested sub-step${suggested === 1 ? " is" : "s are"} waiting in the tree: accept the ones you want, or ask again.`
              : "The model suggests smaller steps; accept the ones you want and this step becomes a group."}
          </p>
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
            disabled={busy}
            placeholder="How to split it (optional)"
            aria-label="Guidance for the model"
            className={INPUT}
          />
          <div className="flex justify-end gap-2">
            <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={() => setOpen(false)}>
              Close
            </button>
            <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={() => void run()}>
              {busy && <Loader2 size={14} className="animate-spin" />}
              {busy ? "Thinking…" : "Suggest sub-steps"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
