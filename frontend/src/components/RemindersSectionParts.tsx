import { ReminderModal } from "./ReminderModal";
import type { RemindersController } from "../lib/useRemindersController";

// The reminders' piece of the app shell's modal layer. The fired-reminders list
// itself stays in `Sidebar`, which reads it from the controller.

// The reminder editor a sidebar row's "Set reminder" action opens.
export function SidebarReminderModal({ controller }: { controller: RemindersController }) {
  const { sidebarReminder, setSidebarReminder, loadReminders } = controller;
  if (!sidebarReminder) return null;
  return (
    <ReminderModal
      container={sidebarReminder.container}
      containerId={sidebarReminder.id}
      existing={null}
      onClose={() => setSidebarReminder(null)}
      onSaved={() => {
        setSidebarReminder(null);
        void loadReminders();
      }}
    />
  );
}
