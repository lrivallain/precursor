import { useState } from "react";
import { Loader2 } from "lucide-react";
import { api } from "../lib/api";
import {
  RecurrenceListEditor,
  defaultRecurrence,
  recurrenceListFromSchedule,
  recurrenceListToPayload,
  type RecurrenceValue,
} from "./RecurrenceEditor";
import type { Workflow } from "../lib/types";

interface Props {
  workflow: Workflow;
  onSaved: (workflow: Workflow) => void;
}

/**
 * Recurrence editor for a workflow.
 *
 * Uses the same {@link RecurrenceListEditor} as scheduled topics and agents, so
 * a workflow can be scheduled with the vocabulary they already had — an
 * arbitrary "every N minutes/hours/days" interval, or a time of day on a chosen
 * set of weekdays — instead of the four fixed presets it started with, and can
 * combine several of those rules at once. Writes the whole schedule block via
 * PUT.
 *
 * Webhook management is *not* here: revoking a webhook has nothing to do with
 * recurrence, so it lives on the webhook control itself.
 */
export function WorkflowScheduleEditor({ workflow, onSaved }: Props) {
  const [enabled, setEnabled] = useState(workflow.schedule_enabled);
  const [recurrence, setRecurrence] = useState<RecurrenceValue[]>(() =>
    workflow.interval_seconds
      ? recurrenceListFromSchedule({
          interval_seconds: workflow.interval_seconds,
          run_at_minute: workflow.run_at_minute,
          days_of_week: workflow.days_of_week,
          rules: workflow.rules,
        })
      : [defaultRecurrence()],
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setError(null);
    try {
      onSaved(
        await api.workflows.setSchedule(workflow.id, {
          schedule_enabled: enabled,
          ...recurrenceListToPayload(recurrence),
        }),
      );
    } catch {
      setError("Failed to save the schedule.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-border bg-bg/40 p-4">
      <label className="flex items-center gap-2 text-sm font-medium text-fg">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="accent-indigo-500"
        />
        Run on a schedule
      </label>

      <div className={`mt-3 ${enabled ? "" : "pointer-events-none opacity-50"}`}>
        <RecurrenceListEditor value={recurrence} onChange={setRecurrence} />
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        {error && <span className="mr-auto text-[11px] text-red-500">{error}</span>}
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving}
          className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-indigo-600 disabled:opacity-40"
        >
          {saving && <Loader2 size={14} className="animate-spin" />}
          Save schedule
        </button>
      </div>
    </div>
  );
}
