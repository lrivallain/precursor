import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileText, Loader2, MessageSquareQuote, Save, Send } from "lucide-react";
import { api } from "../../lib/api";
import { openTopic } from "../../lib/sideChats";
import { hasUnsavedEdits } from "../../lib/stepPlans";
import type { Message, StepPlan } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { Markdown } from "../Markdown";
import { RefineTextarea } from "../RefineTextarea";
import { AssessmentPanel } from "./AssessmentPanel";
import { PlanChat } from "./PlanChat";
import { WorkTabs } from "./WorkTabs";
import { BTN_PRIMARY, BTN_SECONDARY, GuidedAction, INPUT, StaleBadge } from "./StepUi";

type Mutate = <T>(action: () => Promise<T>) => Promise<T | undefined>;

/** Plan-level guidance for the consolidation, saved when the field loses focus. */
export function OutputInstructions({ plan, mutate }: { plan: StepPlan; mutate: Mutate }) {
  const [value, setValue] = useState(plan.output_instructions ?? "");
  useEffect(() => setValue(plan.output_instructions ?? ""), [plan.id, plan.output_instructions]);
  return (
    <label className="block text-xs font-medium text-muted">
      Output instructions
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => {
          const next = value.trim();
          if (next !== (plan.output_instructions ?? "")) {
            void mutate(() => api.stepPlans.update(plan.id, { output_instructions: next || null }));
          }
        }}
        rows={3}
        placeholder="How the final answer should read: tone, language, length, structure…"
        className={`${INPUT} mt-1 resize-y font-normal`}
      />
    </label>
  );
}

/**
 * The last step: one final output assembled from the validated artifacts,
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
  const [draft, setDraft] = useState(finalOutput);
  const [view, setView] = useState<"edit" | "preview">(finalOutput ? "preview" : "edit");
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Message | null>(null);
  const [newer, setNewer] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [adopting, setAdopting] = useState(false);
  // The text the draft started from, so a model-written final output arriving
  // isn't mistaken for the user's unsaved edits.
  const [base, setBase] = useState(finalOutput);
  const dirty = hasUnsavedEdits(draft, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const planIdRef = useRef(plan.id);

  useEffect(() => {
    const samePlan = planIdRef.current === plan.id;
    planIdRef.current = plan.id;
    if (samePlan && dirtyRef.current && finalOutput.trim() !== draftRef.current.trim()) {
      setNewer(true);
    } else {
      setDraft(finalOutput);
      setBase(finalOutput);
      setNewer(false);
    }
  }, [plan.id, finalOutput]);

  const unvalidated = !plan.ready_to_consolidate;
  const hasFinal = finalOutput.trim().length > 0;

  async function save(): Promise<void> {
    if (!dirty || saving) return;
    setSaving(true);
    try {
      await mutate(() => api.stepPlans.writeFinal(plan.id, draft));
    } finally {
      setSaving(false);
    }
  }

  async function generate(guidance: string): Promise<void> {
    if (dirty) {
      const ok = await confirm({
        title: "Discard unsaved edits?",
        message: "Your unsaved edits to the final output will be replaced by the generated text.",
        confirmLabel: "Replace",
        variant: "warning",
      });
      if (!ok) return;
    }
    if (unvalidated) {
      const ok = await confirm({
        title: "Not every step is validated",
        message:
          "Some working steps are not validated yet. The final output will be built from the artifacts that exist, which may leave gaps. Generate anyway?",
        confirmLabel: "Generate anyway",
        variant: "warning",
      });
      if (!ok) return;
    }
    setNotice(null);
    const result = await mutate(() =>
      api.stepPlans.generateFinal(plan.id, { instructions: guidance || undefined, force: unvalidated }),
    );
    if (!result) return;
    if (result.outcome === "published") setView("preview");
    else if (result.outcome === "awaiting_approval") {
      setNotice(
        "The model is waiting for your approval of a tool call in the discussion. Approve or deny it there, then ask again.",
      );
    } else {
      setNotice(
        "The model answered in the discussion without a deliverable (a question or alternatives), so the final output is unchanged. Reply in the discussion, or use its reply as the final output.",
      );
    }
  }

  // Take the latest consolidation-discussion reply as the final output.
  async function useLatestReply(): Promise<void> {
    if (plan.consolidation_chat_id == null || adopting) return;
    if (dirty) {
      const ok = await confirm({
        title: "Discard unsaved edits?",
        message: "Your unsaved edits to the final output will be replaced by the discussion reply.",
        confirmLabel: "Replace",
        variant: "warning",
      });
      if (!ok) return;
    }
    setAdopting(true);
    setNotice(null);
    try {
      const messages = await api.chats.listMessages(plan.consolidation_chat_id);
      const reply = [...messages]
        .reverse()
        .find((m) => m.role === "assistant" && m.kind !== "step_plan" && m.content.trim());
      if (!reply) {
        setNotice("The discussion has no model reply to use yet.");
        return;
      }
      const updated = await mutate(() => api.stepPlans.finalFromMessage(plan.id, reply.id));
      if (updated) setView("preview");
    } finally {
      setAdopting(false);
    }
  }

  async function sendToTopic(): Promise<void> {
    const provisional = plan.final_provisional;
    const ok = await confirm({
      title: provisional ? "Send a provisional output?" : "Send to topic?",
      message: provisional
        ? `Not every step was validated when this output was written. It will be posted in “${plan.source_topic_title ?? "the source topic"}” marked as provisional, and the plan will not be marked done.`
        : `The final output will be posted as a new message in “${plan.source_topic_title ?? "the source topic"}”.`,
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

  const sendBlocker = !plan.source_topic_id
    ? "This plan wasn't started from a topic, so there is nowhere to send it."
    : !hasFinal
      ? "Generate the final output first."
      : dirty
        ? "Save your edits first."
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

      {plan.final_provisional && hasFinal && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          Provisional: this output was written before every step was validated, so it is not an
          approved final and the plan stays open. Validate the remaining steps, then regenerate it.
        </p>
      )}
      {notice && (
        <p role="status" className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
          {notice}
        </p>
      )}

      {unvalidated && (
        <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-muted">
          Some working steps are not validated yet. Validate them first for a complete answer, or
          generate anyway.
        </p>
      )}
      {plan.final_stale && hasFinal && (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          Steps changed after this output was written. Regenerate it, or edit it by hand.
        </p>
      )}
      {sent && plan.source_topic_id && (
        <p className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 size={16} /> Sent to “{plan.source_topic_title}”.
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
        title="Overall assessment"
        assessment={plan.assessment}
        stale={plan.assessment_stale}
        disabledReason={plan.steps.length === 0 ? "Add steps first." : null}
        onAssess={() => mutate(() => api.stepPlans.assessPlan(plan.id))}
      />

      <OutputInstructions plan={plan} mutate={mutate} />

      <GuidedAction
        label={hasFinal ? "Regenerate final output" : "Generate final output"}
        busyLabel="Consolidating…"
        placeholder="e.g. start with a two-line summary, then one section per ask"
        onRun={generate}
      >
        {plan.consolidation_chat_id != null && (
          <button
            type="button"
            className={BTN_SECONDARY}
            disabled={adopting}
            onClick={() => void useLatestReply()}
            data-tooltip="Save the model's latest reply in the consolidation discussion as the final output"
          >
            {adopting ? <Loader2 size={14} className="animate-spin" /> : <MessageSquareQuote size={14} />}
            Use latest discussion reply
          </button>
        )}
      </GuidedAction>

      <WorkTabs
        ariaLabel="Consolidation"
        workLabel="Final output"
        workIcon={<FileText size={14} />}
        discussion={<PlanChat open={() => api.stepPlans.consolidationChat(plan.id)} onOpened={() => void reload()} />}
        work={
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex overflow-hidden rounded-lg border border-border text-xs" role="tablist" aria-label="Final output view">
              {(["edit", "preview"] as const).map((v) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={view === v}
                  onClick={() => setView(v)}
                  className={`px-3 py-1.5 ${view === v ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface"}`}
                >
                  {v === "edit" ? "Edit" : "Preview"}
                </button>
              ))}
            </div>
            {dirty && <span className="text-xs text-muted">unsaved edits</span>}
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                onClick={() => void save()}
                disabled={!dirty || saving}
                className={BTN_SECONDARY}
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} Save
              </button>
              <button
                type="button"
                onClick={() => void sendToTopic()}
                disabled={sendBlocker !== null || sending}
                className={BTN_PRIMARY}
                data-tooltip={
                  sendBlocker ?? "Post the final output as a message in the topic this plan came from"
                }
              >
                {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                Send to topic
              </button>
            </div>
          </div>

          {newer && (
            <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
              A newer final output exists.
              <button
                type="button"
                className="underline"
                onClick={() => {
                  setDraft(finalOutput);
                  setBase(finalOutput);
                  setNewer(false);
                }}
              >
                Discard my edits and load it
              </button>
            </div>
          )}

          {view === "edit" ? (
            <RefineTextarea
              value={draft}
              onValueChange={setDraft}
              refineKind="note"
              markdown
              rows={18}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
                  e.preventDefault();
                  void save();
                }
              }}
              placeholder="The final answer, in Markdown. Generate it from the validated steps, or write it yourself."
              aria-label="Final output (Markdown)"
              className="w-full resize-y rounded-lg border border-border bg-bg px-3 py-2 font-mono text-sm outline-none focus:border-accent/60"
            />
          ) : (
            <div className="min-h-40 rounded-lg border border-border bg-bg px-4 py-3">
              {draft.trim() ? <Markdown>{draft}</Markdown> : <p className="text-sm text-muted">No final output yet.</p>}
            </div>
          )}
        </div>
        }
      />
    </div>
  );
}
