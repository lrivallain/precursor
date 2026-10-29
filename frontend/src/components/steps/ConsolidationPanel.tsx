import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, FileText, Loader2, Save, Send, Sparkles } from "lucide-react";
import { api } from "../../lib/api";
import { openTopic } from "../../lib/sideChats";
import { dropDraft, draftKey, markFailed, planFieldKey, useFieldDraft, useStoredDraft } from "../../lib/stepDrafts";
import { hasUnsavedEdits } from "../../lib/stepPlans";
import type { Message, StepPlan } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { Markdown } from "../Markdown";
import { RefineTextarea } from "../RefineTextarea";
import { AssessmentPanel } from "./AssessmentPanel";
import type { AppliedInfo } from "./ArtifactCard";
import { PlanChat } from "./PlanChat";
import { WorkTabs } from "./WorkTabs";
import { BTN_PRIMARY, BTN_SECONDARY, INPUT, OverflowMenu, StaleBadge } from "./StepUi";

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
  const [view, setView] = useState<"edit" | "preview">(finalOutput ? "preview" : "edit");
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState<Message | null>(null);
  const [newer, setNewer] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [adopting, setAdopting] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [options, setOptions] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [proposals, setProposals] = useState<{ id: number; artifact: string }[]>([]);
  const registerProposal = useCallback((messageId: number, artifact: string) => {
    setProposals((all) => [...all.filter((p) => p.id !== messageId), { id: messageId, artifact }]);
    return () => setProposals((all) => all.filter((p) => p.id !== messageId));
  }, []);
  // The final output keeps no history: a proposal is "applied" while it is
  // the text on file.
  const appliedInfo = useCallback(
    (artifact: string): AppliedInfo | null =>
      artifact.trim() !== "" && artifact.trim() === finalOutput.trim()
        ? { label: "Applied to the final output", current: true }
        : null,
    [finalOutput],
  );
  // The proposal the divider arrow carries: the newest finished reply, unless
  // it is already the final output.
  const newest = proposals.reduce<{ id: number; artifact: string } | null>((a, p) => (!a || p.id > a.id ? p : a), null);
  const latestProposal = newest && !appliedInfo(newest.artifact) ? newest.id : null;
  // The text the draft started from, so a model-written final output arriving
  // isn't mistaken for the user's unsaved edits.
  // Both outlive this panel (see stepDrafts): leaving for a step keeps them.
  const { draft, setDraft, base, setBase } = useStoredDraft(draftKey(plan.id, "final"), finalOutput);
  const dirty = hasUnsavedEdits(draft, base);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const baseRef = useRef(base);
  baseRef.current = base;

  useEffect(() => {
    if (dirtyRef.current && finalOutput.trim() !== draftRef.current.trim()) {
      setNewer(finalOutput !== baseRef.current);
    } else {
      setDraft(finalOutput);
      setBase(finalOutput);
      setNewer(false);
    }
  }, [plan.id, finalOutput]);

  const unvalidated = !plan.ready_to_consolidate;
  const hasFinal = finalOutput.trim().length > 0;
  const assessedFinal = plan.assessment?.scope === "final";

  // Saving unchanged text is how you say "I read this against the current
  // steps and it still holds": it clears the out-of-date flag.
  const reviewable = plan.final_stale && hasFinal && !dirty;

  async function save(): Promise<void> {
    if ((!dirty && !reviewable) || saving) return;
    setSaving(true);
    try {
      await mutate(() => api.stepPlans.writeFinal(plan.id, dirty ? draft : finalOutput));
    } finally {
      setSaving(false);
    }
  }

  async function generate(): Promise<void> {
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
    if (result.outcome === "published") setView("preview");
    else if (result.outcome === "awaiting_approval") {
      setNotice(
        "The model is waiting for your approval of a tool call in the discussion. Approve or deny it there, then ask again.",
      );
    } else {
      setNotice(
        "The model answered in the discussion without a deliverable (a question or alternatives), so the final output is unchanged. Reply in the discussion, or send one of its replies to the final output.",
      );
    }
  }

  // Take a consolidation-discussion reply as the final output.
  async function useReply(messageId: number): Promise<boolean> {
    if (adopting) return false;
    if (dirty) {
      const ok = await confirm({
        title: "Discard unsaved edits?",
        message: "Your unsaved edits to the final output will be replaced by the discussion reply.",
        confirmLabel: "Replace",
        variant: "warning",
      });
      if (!ok) return false;
    }
    setAdopting(true);
    setNotice(null);
    try {
      const updated = await mutate(() => api.stepPlans.finalFromMessage(plan.id, messageId));
      if (updated) {
        setView("preview");
      }
      return !!updated;
    } finally {
      setAdopting(false);
    }
  }

  async function markDone(): Promise<void> {
    await mutate(() => api.stepPlans.complete(plan.id));
  }

  async function sendToTopic(): Promise<void> {
    const provisional = plan.final_provisional;
    const ok = await confirm({
      title: provisional ? "Send a provisional output?" : "Send to topic?",
      message: provisional
        ? `Not every step was approved when this output was written. It will be posted in “${plan.source_topic_title ?? "the source topic"}” marked as provisional, and the plan will not be marked done.`
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

  const doneBlocker = !hasFinal
    ? "Generate the final output first."
    : dirty
      ? "Save your edits first."
      : plan.final_provisional || unvalidated
        ? "Approve every step and regenerate the final output first."
        : plan.final_stale
          ? "The final output is out of date. Regenerate it, or mark it as reviewed, first."
          : null;

  const sendBlocker = !plan.source_topic_id
    ? "This plan wasn't started from a topic, so there is nowhere to send it."
    : !hasFinal
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
        transfer={
          plan.consolidation_chat_id != null
            ? {
                label: "Send the proposal to the final output",
                tooltip:
                  "Send the model's latest proposal to the final output (provisional until every step is approved). Your unsaved edits are kept until you confirm.",
                idleReason:
                  "Nothing to send yet. This lights up when a reply in the discussion proposes a final output.",
                ready: latestProposal !== null,
                onClick: () => (latestProposal !== null ? useReply(latestProposal) : Promise.resolve(false)),
              }
            : undefined
        }
        discussion={
          <PlanChat
            open={() => api.stepPlans.consolidationChat(plan.id)}
            onOpened={() => void reload()}
            artifactAction={{
              label: "Send to final output",
              tooltip: "Save this as the final output (provisional until every step is approved).",
              send: async (id) => void (await useReply(id)),
              register: registerProposal,
              applied: appliedInfo,
            }}
          />
        }
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
                disabled={(!dirty && !reviewable) || saving}
                className={BTN_SECONDARY}
                data-tooltip={
                  reviewable
                    ? "You checked this output against the current steps: clears the out-of-date flag"
                    : dirty
                      ? "Save the final output (⌘/Ctrl+S)"
                      : "Nothing to save"
                }
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}{" "}
                {reviewable ? "Mark as reviewed" : "Save"}
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
              placeholder="The final answer, in Markdown. Generate it from the approved steps, or write it yourself."
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
