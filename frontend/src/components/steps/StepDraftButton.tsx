import { useState } from "react";
import { ChevronDown, Loader2, Sparkles } from "lucide-react";
import { api } from "../../lib/api";
import type { AutomationMode, DraftOutcome, Step } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { BTN_PRIMARY, BTN_SECONDARY, INPUT } from "./StepUi";

export function draftNotice(outcome: DraftOutcome, version: number): string {
  switch (outcome) {
    case "published":
      return `The model wrote version ${version} from the discussion. It still needs your review before you validate it.`;
    case "awaiting_approval":
      return "The model is waiting for your approval of a tool call in the discussion. Approve or deny it there, then draft again.";
    default:
      return "The model answered in the discussion without a deliverable (a question or alternatives), so the artifact is unchanged. Reply in the discussion, or use its reply as the artifact.";
  }
}

/**
 * The step-level automation trigger: the model drafts (or redrafts) this step's
 * artifact from the step's discussion, with its model, role, tools and approval
 * gates. The caret opens an optional steer. It is the main action while a
 * step has nothing yet in an assisted or automated plan.
 */
export function StepDraftButton({
  step,
  mode,
  mutate,
  onNotice,
}: {
  step: Step;
  mode: AutomationMode;
  mutate: <T>(action: () => Promise<T>) => Promise<T | undefined>;
  onNotice: (notice: string | null) => void;
}) {
  const confirm = useConfirm();
  const [open, setOpen] = useState(false);
  const [guidance, setGuidance] = useState("");
  const [busy, setBusy] = useState(false);
  const hasArtifact = step.artifact.trim() !== "";
  const prominent = mode !== "manual" && !hasArtifact;

  async function run(): Promise<void> {
    if (step.status === "validated") {
      const ok = await confirm({
        title: "Redraft a validated step?",
        message:
          "The model's draft becomes a new version and the step goes back to “to validate”. Your approval is lost until you validate it again.",
        confirmLabel: "Redraft",
        variant: "warning",
      });
      if (!ok) return;
    }
    setBusy(true);
    setOpen(false);
    onNotice(null);
    try {
      const result = await mutate(() =>
        api.stepPlans.generateArtifact(step.id, guidance.trim() || undefined),
      );
      if (result) onNotice(draftNotice(result.outcome, result.step.artifact_version));
    } finally {
      setBusy(false);
    }
  }

  const label = hasArtifact ? "Redraft with model" : "Draft with model";
  return (
    <div className="relative">
      <div className="inline-flex">
        <button
          type="button"
          onClick={() => void run()}
          disabled={busy}
          className={`${prominent ? BTN_PRIMARY : BTN_SECONDARY} rounded-r-none`}
          data-tooltip={
            hasArtifact
              ? "Ask the model to rewrite the artifact from this step's discussion. Added as a new version; your edits are kept."
              : "Ask the model to draft this step's artifact from its discussion. You review it before validating."
          }
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {busy ? "Drafting…" : label}
        </button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={busy}
          aria-expanded={open}
          aria-label="Guidance for the model"
          data-tooltip="Add guidance for the model"
          className={`${prominent ? BTN_PRIMARY : BTN_SECONDARY} -ml-px rounded-l-none px-2 ${
            prominent ? "border-l border-white/30" : ""
          }`}
        >
          <ChevronDown size={14} />
        </button>
      </div>
      {open && (
        <div
          role="dialog"
          aria-label="Drafting guidance"
          className="absolute left-0 z-20 mt-2 w-80 space-y-2 rounded-lg border border-border bg-bg p-3 shadow-lg"
        >
          <p className="text-xs text-muted">
            The draft is grounded on this step's discussion, so anything you agreed there counts.
            Add a steer for this run if you want one.
          </p>
          <textarea
            value={guidance}
            onChange={(e) => setGuidance(e.target.value)}
            rows={2}
            placeholder="e.g. keep it under 200 words, answer in French, cite the contract clause…"
            aria-label="Guidance for the model"
            className={INPUT}
          />
          <div className="flex justify-end gap-2">
            <button type="button" className={BTN_SECONDARY} onClick={() => setOpen(false)}>
              Close
            </button>
            <button type="button" className={BTN_PRIMARY} onClick={() => void run()}>
              <Sparkles size={14} /> {label}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
