import { useEffect, useMemo, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { api } from "./api";
import type { Reminder, ReminderContainer } from "./types";

export interface UseRemindersOptions {
  container: ReminderContainer;
  id: number;
  /** Re-fetch the persisted transcript (the backend records set/clear as system messages). */
  reload: () => Promise<unknown>;
  /** Refresh the sidebar Reminders section after a set / cancel / done. */
  onRemindersChanged?: () => void;
  /** Append a local system note to the transcript (surface-specific). */
  systemNote: (content: string) => void;
}

export interface RemindersController {
  reminder: Reminder | null;
  setReminder: Dispatch<SetStateAction<Reminder | null>>;
  reminderModal: { note: string } | null;
  setReminderModal: Dispatch<SetStateAction<{ note: string } | null>>;
  reminderBusy: boolean;
  /** Apply a modal save: update state and pull in the backend confirmation. */
  handleReminderSaved: (saved: Reminder | null) => void;
  /**
   * Shared by /reminder-cancel (any reminder) and /done (a fired one). The
   * backend DELETE is the same; only the guard messages differ.
   */
  runReminderClear: (requireFired: boolean) => Promise<void>;
}

/**
 * One-shot reminder handling for a conversation surface. The topic and chat
 * panels previously duplicated this verbatim; the only delta is the container
 * ("topic" vs "chat"), which the reminders API already parameterizes.
 */
export function useReminders({
  container,
  id,
  reload,
  onRemindersChanged,
  systemNote,
}: UseRemindersOptions): RemindersController {
  const [reminder, setReminder] = useState<Reminder | null>(null);
  const [reminderModal, setReminderModal] = useState<{ note: string } | null>(null);
  const [reminderBusy, setReminderBusy] = useState(false);

  // Load this conversation's reminder (if any) so we can show the fired banner
  // and prefill the edit modal. Re-runs when the conversation changes.
  const refreshReminder = useMemo(
    () => async () => {
      try {
        setReminder(await api.getReminder(container, id));
      } catch {
        setReminder(null); // 404 => no reminder
      }
    },
    [container, id],
  );
  useEffect(() => {
    void refreshReminder();
  }, [refreshReminder]);

  function handleReminderSaved(saved: Reminder | null): void {
    setReminder(saved);
    void reload();
    onRemindersChanged?.();
  }

  async function runReminderClear(requireFired: boolean): Promise<void> {
    if (!reminder) {
      systemNote(requireFired ? "No active reminder to mark done." : "No reminder set.");
      return;
    }
    if (requireFired && reminder.status !== "fired") {
      systemNote("This reminder hasn't fired yet. Use `/reminder-cancel` to remove it.");
      return;
    }
    setReminderBusy(true);
    try {
      await api.clearReminder(container, id);
      setReminder(null);
      await reload();
      onRemindersChanged?.();
    } catch (err) {
      systemNote(`Reminder update failed: ${(err as Error).message}`);
    } finally {
      setReminderBusy(false);
    }
  }

  return {
    reminder,
    setReminder,
    reminderModal,
    setReminderModal,
    reminderBusy,
    handleReminderSaved,
    runReminderClear,
  };
}
