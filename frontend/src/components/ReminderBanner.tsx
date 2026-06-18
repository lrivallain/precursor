import { AlarmClock, Check } from "lucide-react";
import type { Reminder } from "../lib/types";

interface Props {
  reminder: Reminder;
  onDone: () => void;
  busy?: boolean;
}

/**
 * Banner shown at the top of a conversation when its reminder has fired, until
 * the user marks it handled (the "Done" button, equivalent to /done).
 */
export function ReminderBanner({ reminder, onDone, busy }: Props) {
  const note = (reminder.note ?? "").trim();
  return (
    <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-accent/10 text-sm">
      <AlarmClock size={16} className="text-accent shrink-0" />
      <span className="flex-1 min-w-0 truncate">
        <span className="font-medium">Reminder</span>
        {note ? <span className="text-muted">: {note}</span> : null}
      </span>
      <button
        onClick={onDone}
        disabled={busy}
        className="flex items-center gap-1 px-2 py-1 rounded bg-accent text-white text-xs disabled:opacity-50"
        data-tooltip="Mark reminder handled"
      >
        <Check size={13} />
        Done
      </button>
    </div>
  );
}
