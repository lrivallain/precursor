import { useEffect, useState } from "react";
import { api } from "../../lib/api";
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
  const [title, setTitle] = useState(step.title);
  const [instruction, setInstruction] = useState(step.instruction);

  useEffect(() => {
    setTitle(step.title);
    setInstruction(step.instruction);
  }, [step.id, step.title, step.instruction]);

  async function commit(): Promise<void> {
    const nextTitle = title.trim();
    if (!nextTitle) {
      setTitle(step.title);
      return;
    }
    const patch: { title?: string; instruction?: string } = {};
    if (nextTitle !== step.title) patch.title = nextTitle;
    if (instruction.trim() !== step.instruction) patch.instruction = instruction.trim();
    if (Object.keys(patch).length === 0) return;
    await mutate(() => api.stepPlans.updateStep(step.id, patch));
  }

  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-muted">
        Title
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
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
          onChange={(e) => setInstruction(e.target.value)}
          onBlur={() => void commit()}
          readOnly={readOnly}
          rows={compact ? 3 : 4}
          placeholder="What this step should produce, and any constraints."
          className={`${INPUT} mt-1 resize-y font-normal`}
        />
      </label>
    </div>
  );
}
