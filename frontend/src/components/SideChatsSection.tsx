import { useCallback, useEffect, useRef, useState } from "react";
import { AlarmClock, MessageSquarePlus, MessagesSquare, Quote, Split } from "lucide-react";
import { api } from "../lib/api";
import { eventBus } from "../lib/events";
import { openChatById } from "../lib/sideChats";
import type { SideChatItem } from "../lib/types";
import { relativeWhen } from "./ChatStatsReminder";

export interface SideChatsControls {
  items: SideChatItem[];
  /** Start a side chat on the whole topic. */
  onStart: () => void;
  starting: boolean;
}

/**
 * The topic's side chats, kept current from the event bus: a side chat's own
 * activity (a reply, a rename, a read, a reminder) and the topic's changes
 * (a side chat started or removed elsewhere) both refetch the list.
 */
export function useSideChats(topicId: number): SideChatItem[] {
  const [items, setItems] = useState<SideChatItem[]>([]);
  const idsRef = useRef<Set<number>>(new Set());

  const load = useCallback(async () => {
    try {
      const next = await api.sideChats.list(topicId);
      idsRef.current = new Set(next.map((c) => c.id));
      setItems(next);
    } catch {
      // non-fatal: the section just keeps its last list
    }
  }, [topicId]);

  useEffect(() => {
    setItems([]);
    idsRef.current = new Set();
    void load();
  }, [load]);

  useEffect(() => {
    eventBus.start();
    let timer: number | undefined;
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void load(), 300);
    };
    const off = eventBus.subscribe((event) => {
      if (event.type === "topic.changed") {
        if (event.topic_id == null || event.topic_id === topicId) schedule();
        return;
      }
      if (event.type === "meeting.changed" || event.type === "workflow.changed") return;
      const chatId = "chat_id" in event ? event.chat_id : null;
      if (chatId == null) {
        // A bare chat.changed (no id) can be any chat.
        if (event.type === "chat.changed") schedule();
        return;
      }
      if (idsRef.current.has(chatId)) schedule();
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [topicId, load]);

  return items;
}

function lastActivity(item: SideChatItem): string {
  const at = item.last_message_at ?? item.created_at;
  return relativeWhen(new Date(at));
}

export function SideChatsSection({ items, onStart, starting }: SideChatsControls) {
  return (
    <div data-testid="side-chats">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs uppercase tracking-wide text-muted">Side chats</span>
        <button
          type="button"
          onClick={onStart}
          disabled={starting}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-accent hover:bg-accent/10 disabled:opacity-50"
          data-tooltip={"Start a chat linked to this topic\nExplore a tangent without adding it here"}
        >
          <MessageSquarePlus size={12} />
          New
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-xs text-muted">
          None yet. Start one here, or from a reply's toolbar.
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => void openChatById(item.id)}
                className="w-full rounded-md border border-border bg-surface/60 px-2 py-1.5 text-left hover:border-accent/50 hover:bg-surface"
              >
                <div className="flex items-center gap-1.5">
                  {item.from_reply ? (
                    <Quote
                      size={12}
                      className="shrink-0 text-muted"
                      aria-label="Started from a reply"
                    />
                  ) : (
                    <MessagesSquare
                      size={12}
                      className="shrink-0 text-muted"
                      aria-label="Started from the topic"
                    />
                  )}
                  <span
                    className={`min-w-0 flex-1 truncate ${item.unread_count > 0 ? "font-semibold" : ""}`}
                  >
                    {item.title}
                  </span>
                  {item.reminder && (
                    <AlarmClock
                      size={12}
                      className={`shrink-0 ${
                        item.reminder.status === "fired"
                          ? "text-amber-600 dark:text-amber-400"
                          : "text-accent"
                      }`}
                      aria-label={
                        item.reminder.status === "fired" ? "Reminder fired" : "Reminder set"
                      }
                    />
                  )}
                  {item.unread_count > 0 && (
                    <span className="shrink-0 rounded-full bg-accent px-1.5 text-[10px] font-semibold text-white tabular-nums">
                      {item.unread_count}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 flex justify-between gap-2 pl-[18px] text-[11px] text-muted tabular-nums">
                  <span>
                    {item.message_count} {item.message_count === 1 ? "message" : "messages"}
                  </span>
                  <span className="truncate">{lastActivity(item)}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Links under a topic reply to the side chats started from it, so a tangent
 * is one click away from the answer it branched off.
 */
export function SideChatLinks({ items }: { items: SideChatItem[] }) {
  return (
    <div
      className="flex max-w-full flex-wrap items-center gap-1.5 px-1"
      aria-label="Side chats started from this reply"
    >
      {items.map((item) => {
        const fired = item.reminder?.status === "fired";
        const tooltip = [
          `Open side chat “${item.title}”`,
          `${item.message_count} ${item.message_count === 1 ? "message" : "messages"} · ${lastActivity(item)}`,
          item.unread_count > 0 ? `${item.unread_count} unread` : null,
          item.reminder ? (fired ? "Reminder fired" : "Reminder set") : null,
        ]
          .filter(Boolean)
          .join("\n");
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => void openChatById(item.id)}
            className="inline-flex max-w-[18rem] items-center gap-1 rounded-full border border-accent/30 bg-accent/[0.06] px-2 py-0.5 text-[11px] text-accent hover:border-accent/60 hover:bg-accent/10"
            data-tooltip={tooltip}
          >
            <Split size={11} className="shrink-0" />
            <span className={`truncate ${item.unread_count > 0 ? "font-semibold" : ""}`}>
              {item.title}
            </span>
            {item.reminder && (
              <AlarmClock
                size={11}
                className={`shrink-0 ${fired ? "text-amber-600 dark:text-amber-400" : ""}`}
                aria-label={fired ? "Reminder fired" : "Reminder set"}
              />
            )}
            {item.unread_count > 0 && (
              <span className="shrink-0 rounded-full bg-accent px-1 text-[10px] font-semibold leading-4 text-white tabular-nums">
                {item.unread_count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
