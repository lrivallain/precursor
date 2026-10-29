import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, FileText, Loader2, Send, Sparkles } from "lucide-react";
import { api } from "../../lib/api";
import { openTopic } from "../../lib/sideChats";
import { dropDraft, markFailed, planFieldKey, useFieldDraft } from "../../lib/stepDrafts";
import type { Message, StepPlan } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { AssessmentPanel } from "./AssessmentPanel";
import { DocumentEditor, useDocumentDraft } from "./DocumentEditor";
import { PlanChat } from "./PlanChat";
import { draftNotice } from "./StepDraftButton";
import { BTN_PRIMARY, BTN_SECONDARY, INPUT, OverflowMenu, StaleBadge } from "./StepUi";
import { useProposals } from "./useProposals";
import { WorkTabs } from "./WorkTabs";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/** Plan-level guidance for the consolidation, saved when the field loses focus. */
export function OutputInstructions({ plan, mutate }: { plan: StepPlan; mutate: Mutate }) {
  const key = planFieldKey(plan.id, "instructions");
  const field = useFieldDraft(key, plan.output_instructions ?? "");

  async function commit(): Promise<void> {
    const next = field.value.trim();
    if (next === (plan.output_instructions ?? "")) return;
    const saved = await mutate(() => api.stepPlans.update(plan.id, { output_instructions: next || null }));
    if (saved === undefined) markFailed([key], true);
    else dropDraft(key);
  }

  return (
    <label className="block text-xs font-medium text-muted">
      Output instructions
      <textarea
        value={field.value}
        onChange={(e) => field.setValue(e.target.value)}
        onBlur={() => void commit()}
        rows={3}
        placeholder="How the final answer should read: tone, language, length, structure…"
        className={`${INPUT} mt-1 resize-y font-normal`}
      />
      {field.failed && (
        <span role="alert" className="mt-1 flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          Not saved: your text is kept here.
          <button type="button" className="underline" onClick={() => void commit()}>
            Retry
          </button>
        </span>
      )}
    </label>
  );
}

/**
 * The last step: one final output assembled from the approved artifacts,
 * editable, discussable, and fileable back into the topic the ask came from.
 */
export function ConsolidationPanel({
  plan,
  mutate,
  reload,
}: {
  plan: StepPlan;
  mutate: Mutate;
  reload: () => Promise<void>;
}) {
  const confirm = useConfirm();
  const finalOutput = plan.final_output ?? "";
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Message | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [options, setOptions] = useState(false);
  const [guidance, setGuidance] = useState("");
  const doc = useDocumentDraft(plan.id, "final", finalOutput);
  const { dirty } = doc;
  const { versions, transfer, artifactAction } = useProposals({
    load: () => api.stepPlans.finalVersions(plan.id),
    refreshKey: `${plan.id}:${plan.final_version}:${plan.final_output}:${plan.final_posted_version}`,
    current: plan.final_version,
    hasChat: plan.consolidation_chat_id != null,
    send: adoptReply,
    noun: "final output",
    sendTooltip: "Save this as the final output (provisional until every step is approved).",
  });

  const unvalidated = !plan.ready_to_consolidate;
  const hasFinal = finalOutput.trim().length > 0;
  const assessedFinal = plan.assessment?.scope === "final";

  // Saving unchanged text is how you say "I read this against the current
  // steps and it still holds": it clears the out-of-date flag.
  const reviewable = plan.final_stale && hasFinal && !dirty;

  async function generate(): Promise<void> {
    if (unvalidated) {
      const ok = await confirm({
        title: "Not every step is approved",
        message:
          "Some working steps are not approved yet. The final output will be built from the artifacts that exist, which may leave gaps. Generate anyway?",
        confirmLabel: "Generate anyway",
        variant: "warning",
      });
      if (!ok) return;
    }
    setNotice(null);
    setGenerating(true);
    let result;
    try {
      result = await mutate(() =>
        api.stepPlans.generateFinal(plan.id, { instructions: guidance.trim() || undefined, force: unvalidated }),
      );
    } finally {
      setGenerating(false);
    }
    if (!result) return;
    setNotice(draftNotice(result.outcome, result.plan.final_version, "final output", "send it or finish the plan"));
  }

  // Take a consolidation-discussion reply as the final output; unsaved edits
  // stay behind the editor's "newer version" banner.
  async function adoptReply(messageId: number): Promise<boolean> {
    setNotice(null);
    return !!(await mutate(() => api.stepPlans.finalFromMessage(plan.id, messageId)));
  }

  async function sendToTopic(): Promise<void> {
    const provisional = plan.final_provisional;
    const again = plan.final_posted_version === plan.final_version;
    const ok = await confirm({
      title: provisional ? "Send a provisional output?" : "Send to topic?",
      message: provisional
        ? `Not every step was approved when this output was written. It will be posted in “${plan.source_topic_title ?? "the source topic"}” marked as provisional, and the plan will not be marked done.`
        : `Version ${plan.final_version} of the final output will be posted as a new message in “${plan.source_topic_title ?? "the source topic"}”.${again ? " This version was already posted once." : ""}`,
      confirmLabel: provisional ? "Send as provisional" : "Send",
      variant: provisional ? "warning" : undefined,
    });
    if (!ok) return;
    setSending(true);
    try {
      const message = await mutate(() => api.stepPlans.sendToTopic(plan.id, undefined, provisional));
      if (message) setSent(message);
    } finally {
      setSending(false);
    }
  }

  async function markDone(): Promise<void> {
    await mutate(() => api.stepPlans.complete(plan.id));
  }

  const doneBlocker = !hasFinal
    ? "Generate the final output first."
    : dirty
      ? "Save your edits first."
      : plan.final_provisional || unvalidated
        ? "Approve every step and regenerate the final output first."
        : plan.final_stale
          ? "The final output is out of date. Regenerate it, or mark it as reviewed, first."
          : null;

  const sendBlocker = !hasFinal
      ? "Generate the final output first."
      : dirty
        ? "Save your edits first."
        : plan.final_stale
          ? "The steps changed since this output was written. Regenerate it, or review it and mark it as reviewed first."
          : null;

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center gap-2">
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">Final output</h2>
        {plan.final_provisional && hasFinal && (
          <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
            Provisional
          </span>
        )}
        {plan.final_stale && <StaleBadge />}
      </div>

      {notice && (
        <p role="status" className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
          {notice}
        </p>
      )}
      {plan.status === "done" ? (
        <p className="flex items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={16} /> This plan is done.
        </p>
      ) : plan.final_provisional && hasFinal ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          Provisional: written before every step was approved, so the plan stays open. Approve the
          remaining steps, then regenerate it.
        </p>
      ) : plan.final_stale && hasFinal ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          Steps changed after this output was written, so it can't be sent or completed as is.
          Regenerate it, or read it against the current steps and mark it as reviewed.
        </p>
      ) : unvalidated ? (
        <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
          Some steps are not approved yet. Approve them for a complete answer, or generate anyway.
        </p>
      ) : null}
      {sent && plan.source_topic_id && (
        <p className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={16} /> Sent to “{plan.source_topic_title}”
          {plan.final_posted_version ? ` as version ${plan.final_posted_version}` : ""}.
          <button
            type="button"
            className="underline"
            onClick={() => openTopic(plan.source_topic_id!, sent.id)}
          >
            Open the topic
          </button>
        </p>
      )}

      <AssessmentPanel
        title={assessedFinal ? "Final output assessment" : "Plan readiness"}
        assessment={plan.assessment}
        stale={plan.assessment_stale}
        disabledReason={plan.steps.length === 0 ? "Add steps first." : null}
        onAssess={() => mutate(() => api.stepPlans.assessPlan(plan.id))}
        scopeNote={
          assessedFinal
            ? "Scope: the final output as written, against the ask, the output instructions and the step artifacts."
            : hasFinal
              ? "Scope: the steps only, from before the final output existed. It does NOT assess the final output: re-assess to score it."
              : "Scope: the steps only (readiness to answer the ask). There is no final output yet, so this does NOT assess a final deliverable."
        }
      />

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={hasFinal ? BTN_SECONDARY : BTN_PRIMARY}
            disabled={generating}
            onClick={() => void generate()}
            data-testid="generate-final"
          >
            {generating ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {generating ? "Consolidating…" : hasFinal ? "Regenerate" : "Generate the final output"}
          </button>
          <button
            type="button"
            onClick={() => setOptions((v) => !v)}
            aria-expanded={options}
            className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg"
          >
            {options ? <ChevronDown size={12} /> : <ChevronRight size={12} />} Options
          </button>
        </div>
        {options && (
          <div className="space-y-3 rounded-lg border border-border bg-surface p-3">
            <OutputInstructions plan={plan} mutate={mutate} />
            <label className="block text-xs font-medium text-muted">
              Guidance for this run only
              <textarea
                value={guidance}
                onChange={(e) => setGuidance(e.target.value)}
                rows={2}
                placeholder="e.g. start with a two-line summary, then one section per ask"
                className={`${INPUT} mt-1 resize-y font-normal`}
              />
            </label>
          </div>
        )}
      </div>

      <WorkTabs
        ariaLabel="Consolidation"
        workLabel="Final output"
        workIcon={<FileText size={14} />}
        transfer={transfer}
        discussion={
          <PlanChat
            open={() => api.stepPlans.consolidationChat(plan.id)}
            onOpened={() => void reload()}
            artifactAction={artifactAction}
          />
        }
        work={
          <DocumentEditor
            doc={doc}
            persisted={finalOutput}
            version={plan.final_version}
            versions={versions}
            noun="final output"
            placeholder="The final answer, in Markdown. Generate it from the approved steps, or write it yourself."
            onSave={(text) => mutate(() => api.stepPlans.writeFinal(plan.id, text))}
            onRestore={(v) => mutate(() => api.stepPlans.revertFinal(plan.id, v))}
            unchangedSave={
              reviewable
                ? {
                    label: "Mark as reviewed",
                    tooltip: "You checked this output against the current steps: clears the out-of-date flag",
                  }
                : undefined
            }
            status={
              plan.final_posted_version != null && plan.final_version > 0 ? (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300"
                  data-tooltip={`Version ${plan.final_posted_version} was posted in the topic${plan.final_posted_at ? ` on ${new Date(plan.final_posted_at).toLocaleString()}` : ""}`}
                >
                  <Send size={10} />
                  {plan.final_posted_version === plan.final_version ? "posted" : `v${plan.final_posted_version} posted`}
                </span>
              ) : undefined
            }
            actions={
              <>
                {plan.source_topic_id != null && (
                  <button
                    type="button"
                    onClick={() => void sendToTopic()}
                    disabled={sendBlocker !== null || sending}
                    className={BTN_PRIMARY}
                    data-tooltip={sendBlocker ?? "Post the final output as a message in the topic this plan came from"}
                  >
                    {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                    Send to topic
                  </button>
                )}
                {plan.status !== "done" && (
                  <OverflowMenu
                    label="Final output actions"
                    items={[
                      {
                        label: "Mark the plan as done",
                        icon: CheckCircle2,
                        onSelect: markDone,
                        disabledReason: doneBlocker ?? undefined,
                      },
                    ]}
                  />
                )}
              </>
            }
          />
        }
      />
    </div>
  );
}
