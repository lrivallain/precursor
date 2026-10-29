import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { api } from "../../lib/api";
import type { DraftOutcome, Step } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { BTN_PRIMARY, BTN_SECONDARY, INPUT } from "./StepUi";

export function draftNotice(outcome: DraftOutcome, version: number): string {
  switch (outcome) {
    case "published":
      return `The model wrote version ${version} from the discussion. It still needs your review before you approve it.`;
    case "awaiting_approval":
      return "The model is waiting for your approval of a tool call in the discussion. Approve or deny it there, then draft again.";
    default:
      return "The model answered in the discussion without a deliverable (a question or alternatives), so the artifact is unchanged. Reply in the discussion, or send its proposal to the artifact.";
  }
}

/**
 * The step-level automation trigger: the model drafts (or redrafts) this step's
 * artifact from the step's discussion, with its model, role, tools and approval
 * gates. `run` is the one entry point, used by the step's main button and by
 * the guided draft in its menu.
 */
export function useStepDraft({
  step,
  mutate,
  onNotice,
}: {
  step: Step;
  mutate: <T>(action: () => Promise<T>) => Promise<T | undefined>;
  onNotice: (notice: string | null) => void;
}): { busy: boolean; run: (guidance?: string) => Promise<boolean> } {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(false);

  async function run(guidance?: string): Promise<boolean> {
    if (step.status === "validated") {
      const ok = await confirm({
        title: "Redraft an approved step?",
        message:
          "The model's draft becomes a new version and the step goes back to review. Your approval is lost until you approve it again.",
        confirmLabel: "Redraft",
        variant: "warning",
      });
      if (!ok) return false;
    }
    setBusy(true);
    onNotice(null);
    try {
      const result = await mutate(() => api.stepPlans.generateArtifact(step.id, guidance?.trim() || undefined));
      if (result) onNotice(draftNotice(result.outcome, result.step.artifact_version));
      return !!result;
    } finally {
      setBusy(false);
    }
  }

  return { busy, run };
}

/** An inline panel with one optional steer for a model action (drafting, splitting). */
export function GuidancePanel({
  label,
  hint,
  placeholder,
  actionLabel,
  busyLabel,
  busy,
  onRun,
  onClose,
}: {
  label: string;
  hint: string;
  placeholder: string;
  actionLabel: string;
  busyLabel: string;
  busy: boolean;
  onRun: (guidance: string) => void | Promise<void>;
  onClose: () => void;
}) {
  const [guidance, setGuidance] = useState("");
  return (
    <div role="group" aria-label={label} className="space-y-2 rounded-lg border border-border bg-surface/40 p-3">
      <p className="text-xs text-muted">{hint}</p>
      <textarea
        value={guidance}
        onChange={(e) => setGuidance(e.target.value)}
        rows={2}
        disabled={busy}
        placeholder={placeholder}
        aria-label="Guidance for the model"
        className={INPUT}
      />
      <div className="flex justify-end gap-2">
        <button type="button" className={BTN_SECONDARY} disabled={busy} onClick={onClose}>
          Close
        </button>
        <button type="button" className={BTN_PRIMARY} disabled={busy} onClick={() => void onRun(guidance)}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {busy ? busyLabel : actionLabel}
        </button>
      </div>
    </div>
  );
}
