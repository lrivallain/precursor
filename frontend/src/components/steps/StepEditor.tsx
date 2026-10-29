import { api } from "../../lib/api";
import { dropDraft, markFailed, stepFieldKey, useFieldDraft } from "../../lib/stepDrafts";
import type { Step } from "../../lib/types";
import { INPUT } from "./StepUi";

/**
 * Title + instruction of one step. Edits are saved when a field loses focus
 * (or Enter in the title), so there's no separate save button to forget.
 */
export function StepEditor({
  step,
  mutate,
  readOnly = false,
  compact = false,
}: {
  step: Step;
  mutate: <T>(action: () => Promise<T>) => Promise<T | undefined>;
  readOnly?: boolean;
  compact?: boolean;
}) {
  const titleKey = stepFieldKey(step.plan_id, step.id, "title");
  const instructionKey = stepFieldKey(step.plan_id, step.id, "instruction");
  const titleField = useFieldDraft(titleKey, step.title);
  const instructionField = useFieldDraft(instructionKey, step.instruction);
  const title = titleField.value;
  const instruction = instructionField.value;
  const failed = titleField.failed || instructionField.failed;

  async function commit(): Promise<void> {
    const nextTitle = title.trim();
    if (!nextTitle) {
      titleField.setValue(step.title);
      return;
    }
    const patch: { title?: string; instruction?: string } = {};
    const keys: string[] = [];
    if (nextTitle !== step.title) {
      patch.title = nextTitle;
      keys.push(titleKey);
    }
    if (instruction.trim() !== step.instruction) {
      patch.instruction = instruction.trim();
      keys.push(instructionKey);
    }
    if (keys.length === 0) return;
    // A failed save keeps the typed text (in the draft store, so it survives
    // navigating away) and flags it for a retry; the API's reason is the banner.
    const saved = await mutate(() => api.stepPlans.updateStep(step.id, patch));
    if (saved === undefined) markFailed(keys, true);
    else keys.forEach(dropDraft);
  }

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-muted">
        Title
        <input
          value={title}
          onChange={(e) => titleField.setValue(e.target.value)}
          onBlur={() => void commit()}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          readOnly={readOnly}
          maxLength={255}
          className={`${INPUT} mt-1 font-medium`}
        />
      </label>
      <label className="block text-xs font-medium text-muted">
        Instruction
        <textarea
          value={instruction}
          onChange={(e) => instructionField.setValue(e.target.value)}
          onBlur={() => void commit()}
          readOnly={readOnly}
          rows={compact ? 3 : 4}
          placeholder="What this step should produce, and any constraints."
          className={`${INPUT} mt-1 resize-y font-normal`}
        />
      </label>
      {failed && !readOnly && (
        <p role="alert" className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          Not saved: your text is kept here.
          <button type="button" className="underline" onClick={() => void commit()}>
            Retry
          </button>
        </p>
      )}
    </div>
  );
}
