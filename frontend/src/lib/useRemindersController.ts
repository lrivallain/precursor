import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import { api } from "./api";
import { eventBus } from "./events";
import { notifyIfUnfocused } from "./notifications";
import type { ReminderItem } from "./types";
import type { ChatsController } from "./useChatsController";
import type { TopicsController } from "./useTopicsController";

// What the reminders read or drive. The once-registered event bus subscription
// below and App's early mount effect keep the first render's `loadReminders`,
// so it only touches refs and state setters.
export interface RemindersControllerDeps {
  notificationsEnabledRef: RefObject<boolean>;
  topics: TopicsController;
  chats: ChatsController;
  confirmLeaveRecording: () => Promise<boolean>;
  // Leaves home or the current section for Topics or Chats without writing the
  // address bar: the caller selects the conversation in the same batch.
  enterSection: (mode: "topics" | "chats") => void;
}

// The conversation a sidebar "Set reminder" action opened the modal for.
export interface SidebarReminderTarget {
  container: "topic" | "chat";
  id: number;
}

export interface RemindersController {
  reminders: ReminderItem[];
  sidebarReminder: SidebarReminderTarget | null;
  setSidebarReminder: Dispatch<SetStateAction<SidebarReminderTarget | null>>;
  reminderTopicIds: Set<number>;
  reminderChatIds: Set<number>;
  loadReminders: () => Promise<void>;
  handleReminderSelect: (item: ReminderItem) => Promise<void>;
  handleReminderDone: (item: ReminderItem) => Promise<void>;
}

export function useRemindersController(deps: RemindersControllerDeps): RemindersController {
  const { notificationsEnabledRef, topics, chats, confirmLeaveRecording, enterSection } = deps;

  // Fired reminders awaiting acknowledgment, surfaced in the sidebar.
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [sidebarReminder, setSidebarReminder] = useState<SidebarReminderTarget | null>(null);
  // Ids already seen as fired, so we only notify on newly-fired ones.
  const seenFiredRef = useRef<Set<number>>(new Set());
  // Conversations with a fired reminder, so their list rows can flag it.
  const reminderTopicIds = useMemo(
    () =>
      new Set(
        reminders.filter((r) => r.container === "topic" && r.topic_id != null).map((r) => r.topic_id!),
      ),
    [reminders],
  );
  const reminderChatIds = useMemo(
    () =>
      new Set(
        reminders.filter((r) => r.container === "chat" && r.chat_id != null).map((r) => r.chat_id!),
      ),
    [reminders],
  );

  // Reload fired reminders and fire a browser notification for any that became
  // fired since the last load (when enabled + window unfocused).
  async function loadReminders(): Promise<void> {
    let items: ReminderItem[];
    try {
      items = await api.reminders.list();
    } catch {
      return; // transient — keep the previous list
    }
    if (notificationsEnabledRef.current) {
      for (const item of items) {
        if (!seenFiredRef.current.has(item.id)) {
          notifyIfUnfocused({
            title: item.title,
            body: item.note?.trim() ? `⏰ ${item.note.trim()}` : "⏰ Reminder",
            tag: `precursor-reminder-${item.id}`,
          });
        }
      }
    }
    seenFiredRef.current = new Set(items.map((i) => i.id));
    setReminders(items);
  }

  // Live sync across windows: any mutation in another tab/process pushes an
  // event over /api/events. Echoes (events tagged with our own client id)
  // are filtered out inside the bus. The section controllers handle their own
  // events; this one handles the reminders.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
      if (event.type === "reminder.changed") {
        // A reminder was set, fired, or cleared (possibly by the background
        // ticker). Reload the sidebar section; loadReminders also notifies for
        // any newly-fired ones.
        void loadReminders();
      }
    });
    return () => {
      off();
    };
  }, []);

  // Open the conversation behind a fired reminder, switching mode if needed.
  // Fetch it first, then switch and select in one batch: switching first would
  // push an entry for whatever that section had open, then another for this.
  async function handleReminderSelect(item: ReminderItem): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    try {
      if (item.container === "topic" && item.topic_id != null) {
        const topic = await api.topics.get(item.topic_id);
        enterSection("topics");
        await topics.selectTopic(topic);
      } else if (item.container === "chat" && item.chat_id != null) {
        const chat = await api.chats.get(item.chat_id);
        enterSection("chats");
        await chats.handleSelectChat(chat);
      }
    } catch {
      // conversation may have been deleted — refresh the list to drop it
      void loadReminders();
    }
  }

  // Acknowledge a fired reminder ("Done"): clear it and refresh the section.
  async function handleReminderDone(item: ReminderItem): Promise<void> {
    const id = item.container === "topic" ? item.topic_id : item.chat_id;
    if (id == null) return;
    try {
      await api.reminders.clear(item.container, id);
    } catch {
      // already gone — fall through to reload
    }
    await loadReminders();
    // Remount the active panel so its banner clears if it was the one acked.
    if (item.container === "topic" && topics.activeTopicRef.current?.id === item.topic_id) {
      topics.setChatReloadKey((k) => k + 1);
    } else if (item.container === "chat" && chats.activeChatRef.current?.id === item.chat_id) {
      chats.setActiveChatReloadKey((k) => k + 1);
    }
  }

  return {
    reminders,
    sidebarReminder,
    setSidebarReminder,
    reminderTopicIds,
    reminderChatIds,
    loadReminders,
    handleReminderSelect,
    handleReminderDone,
  };
}
