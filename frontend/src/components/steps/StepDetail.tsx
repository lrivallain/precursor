import { useState } from "react";
import { FileText, Loader2, RotateCcw, Scissors, Sparkles, XCircle, CheckCircle2, Undo2 } from "lucide-react";
import { api } from "../../lib/api";
import type { AutomationMode, SettableStepStatus } from "../../lib/types";
import { addChildBlocker, isGroup, primaryAction, progressOf, type StepNode } from "../../lib/stepPlans";
import { useConfirm } from "../ConfirmDialog";
import { PlanChat } from "./PlanChat";
import { DocumentEditor, useDocumentDraft } from "./DocumentEditor";
import { StepEditor } from "./StepEditor";
import { useProposals } from "./useProposals";
import { WorkTabs } from "./WorkTabs";
import { AssessmentPanel } from "./AssessmentPanel";
import { GuidancePanel, useStepDraft } from "./StepDraftButton";
import {
  BTN_PRIMARY,
  BTN_SECONDARY,
  OverflowMenu,
  StepStatusPill,
  type MenuItem,
} from "./StepUi";
import type { Step } from "../../lib/types";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;
type Panel = "draft" | "split" | null;

/** The selected step: one main action to move it forward, a menu for the rest, and its artifact beside its discussion. */
export function StepDetail({
  node,
  mode,
  mutate,
  reload,
  onSelect,
}: {
  node: StepNode;
  mode: AutomationMode;
  mutate: Mutate;
  reload: () => Promise<void>;
  onSelect: (id: number) => void;
}) {
  const { step } = node;
  const group = isGroup(node);
  const confirm = useConfirm();
  const suggested = node.children.filter((c) => c.step.status === "proposed").length;
  const [note, setNote] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [splitting, setSplitting] = useState(false);
  const draft = useStepDraft({ step, mutate, onNotice: setNote });
  const doc = useDocumentDraft(step.plan_id, step.id, step.artifact);
  const { versions, transfer, artifactAction } = useProposals({
    load: () => api.stepPlans.versions(step.id),
    refreshKey: `${step.id}:${step.artifact_version}:${step.artifact}`,
    current: step.artifact_version,
    hasChat: step.chat_id != null,
    send: sendProposal,
    noun: "artifact",
    sendTooltip: "Save this as a new version of the step's artifact. Your unsaved edits are kept.",
  });

  // A proposal becomes a new artifact version; unsaved edits in the editor are
  // kept behind its "newer version" banner.
  async function sendProposal(messageId: number): Promise<boolean> {
    setNote(null);
    const updated = await mutate(() => api.stepPlans.artifactFromMessage(step.id, messageId));
    if (updated) {
      setNote(`Sent to the artifact as version ${updated.artifact_version}. Review it, then approve.`);
    }
    return !!updated;
  }

  async function setStatus(status: SettableStepStatus): Promise<void> {
    if (status === "cancelled") {
      const ok = await confirm({
        title: group ? "Skip this group?" : "Skip this step?",
        message: group
          ? "The group and all its sub-steps will be skipped. You can restore them later."
          : "A skipped step no longer counts toward the final output. You can restore it later.",
        confirmLabel: "Skip",
        cancelLabel: "Keep",
        variant: "warning",
      });
      if (!ok) return;
    }
    await mutate(() => api.stepPlans.setStepStatus(step.id, status));
  }

  if (step.status === "proposed") {
    return (
      <div className="space-y-4">
        <StepHeading step={step} />
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

  const primary = primaryAction(node);
  const splitBlocker = addChildBlocker(node);
  const { done, total } = progressOf(node);
  const skipped = step.effective_status === "cancelled";
  const hasArtifact = step.artifact.trim() !== "";
  const prominentDraft = mode !== "manual";

  function runPrimary(): void {
    if (!primary) return;
    if (primary.kind === "draft") void draft.run();
    else if (primary.kind === "approve") void setStatus("validated");
    else if (primary.kind === "reopen") void setStatus("in_progress");
    else void setStatus("open");
  }

  const menu: MenuItem[] = [];
  if (!group && !skipped) {
    menu.push({
      label: hasArtifact ? "Redraft with guidance…" : "Draft with guidance…",
      icon: Sparkles,
      onSelect: () => setPanel("draft"),
    });
    menu.push({
      label: suggested > 0 ? `Split into sub-steps… (${suggested} suggested)` : "Split into sub-steps…",
      icon: Scissors,
      onSelect: () => setPanel("split"),
      disabledReason: splitBlocker ?? undefined,
    });
  }
  if (group && !skipped) {
    menu.push({ label: "Skip this group", icon: XCircle, danger: true, onSelect: () => setStatus("cancelled") });
  } else if (group && skipped) {
    menu.push({ label: "Restore this group", icon: Undo2, onSelect: () => setStatus("open") });
  } else if (!skipped) {
    menu.push({
      label: "Skip this step",
      icon: XCircle,
      danger: true,
      separated: true,
      onSelect: () => setStatus("cancelled"),
    });
  }

  const PrimaryIcon =
    primary?.kind === "draft" ? Sparkles : primary?.kind === "approve" ? CheckCircle2 : RotateCcw;
  const draftBusy = draft.busy && primary?.kind === "draft";

  return (
    <div className="flex h-full flex-col gap-4">
      <StepHeading step={step} />
      {step.needs_review && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          The ask or this step's instruction changed after you approved it, so the approval no
          longer counts. Review the artifact, then approve it again.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Step actions">
        {primary && (
          <button
            type="button"
            disabled={draft.busy}
            onClick={runPrimary}
            className={primary.kind === "approve" || (primary.kind === "draft" && prominentDraft) ? BTN_PRIMARY : BTN_SECONDARY}
            data-tooltip={primaryTooltip(primary.kind)}
          >
            {draftBusy ? <Loader2 size={14} className="animate-spin" /> : <PrimaryIcon size={14} />}
            {draftBusy ? "Drafting…" : primary.label}
          </button>
        )}
        {!group && <p className="min-w-0 flex-1 text-xs text-muted">{nextHint(step, mode)}</p>}
        <div className="ml-auto">
          <OverflowMenu items={menu} label="More step actions" />
        </div>
      </div>

      {panel === "draft" && !group && (
        <GuidancePanel
          label="Drafting guidance"
          hint="The draft is grounded on this step's discussion, so anything you agreed there counts. Add a steer for this run if you want one."
          placeholder="e.g. keep it under 200 words, answer in French, cite the contract clause…"
          actionLabel={hasArtifact ? "Redraft with model" : "Draft with model"}
          busyLabel="Drafting…"
          busy={draft.busy}
          onRun={async (guidance) => {
            if (await draft.run(guidance)) setPanel(null);
          }}
          onClose={() => setPanel(null)}
        />
      )}
      {panel === "split" && !group && (
        <GuidancePanel
          label="Split into sub-steps"
          hint={
            suggested > 0
              ? `${suggested} suggested sub-step${suggested === 1 ? " is" : "s are"} waiting in the tree: accept the ones you want, or ask again.`
              : "Too big for one artifact? The model suggests smaller steps; accept the ones you want and this step becomes a group."
          }
          placeholder="How to split it (optional)"
          actionLabel="Suggest sub-steps"
          busyLabel="Thinking…"
          busy={splitting}
          onRun={async (guidance) => {
            setSplitting(true);
            try {
              await mutate(() => api.stepPlans.suggestSubsteps(step.id, guidance.trim() || undefined));
              setPanel(null);
            } finally {
              setSplitting(false);
            }
          }}
          onClose={() => setPanel(null)}
        />
      )}
      {note && !group && (
        <p role="status" className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
          {note}
        </p>
      )}

      {group ? (
        <StepEditor step={step} mutate={mutate} />
      ) : (
        <details className="rounded-lg border border-border px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-muted">Title and instruction</summary>
          <div className="pt-2">
            <StepEditor step={step} mutate={mutate} compact />
          </div>
        </details>
      )}

      {group ? (
        <section className="space-y-2 rounded-lg border border-border p-3" aria-label="Sub-steps">
          <h4 className="text-xs font-medium uppercase tracking-wide text-muted">
            Sub-steps · {done}/{total} approved
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
                  <StepStatusPill step={c.step} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <>
          {(step.assessment || hasArtifact) && (
            <AssessmentPanel
              title="Assessment"
              assessment={step.assessment}
              stale={step.assessment_stale}
              disabledReason={hasArtifact ? null : "Write or generate the artifact first."}
              onAssess={() => mutate(() => api.stepPlans.assessStep(step.id))}
            />
          )}
          <WorkTabs
            ariaLabel="Step work"
            workLabel="Artifact"
            workIcon={<FileText size={14} />}
            transfer={transfer}
            work={
              <DocumentEditor
                doc={doc}
                persisted={step.artifact}
                version={step.artifact_version}
                versions={versions}
                noun="artifact"
                placeholder="Write this step's result in Markdown, or draft it with the model (button above)."
                onSave={(text) => mutate(() => api.stepPlans.writeArtifact(step.id, text))}
                onRestore={(v) => mutate(() => api.stepPlans.revertArtifact(step.id, v))}
              />
            }
            discussion={
              <PlanChat
                key={step.id}
                open={() => api.stepPlans.stepChat(step.id)}
                onOpened={() => void reload()}
                artifactAction={artifactAction}
              />
            }
          />
        </>
      )}
    </div>
  );
}

function primaryTooltip(kind: string): string {
  switch (kind) {
    case "draft":
      return "The model drafts this step's artifact from its discussion. You review it before approving.";
    case "approve":
      return "You are happy with the artifact: it counts toward the final output.";
    case "reopen":
      return "Take the approval back to change the artifact.";
    default:
      return "Put this step back in the plan.";
  }
}

/** What to do next with this step, so the flow (draft, review, approve) is never a guess. */
function nextHint(step: Step, mode: AutomationMode): string {
  if (step.effective_status === "cancelled") return "Skipped. Restore it to work on it again.";
  if (step.status === "validated" && !step.needs_review) return "Approved. Reopen it to change it.";
  if (step.artifact.trim() === "") {
    return mode === "manual"
      ? "Write the artifact yourself, or let the model draft it."
      : "Let the model draft it from the discussion. You review it, then approve.";
  }
  return step.created_by === "model"
    ? "Review the model's draft: edit it, discuss it or redraft it, then approve."
    : "Review the artifact, then approve it.";
}

function StepHeading({ step }: { step: Step }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{step.title}</h2>
      <StepStatusPill step={step} />
    </div>
  );
}
