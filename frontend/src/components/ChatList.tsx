import { useEffect, useMemo, useState } from "react";
import { AlarmClock, Loader2, MessageSquare, Pin, Search, Settings2 } from "lucide-react";
import { api } from "../lib/api";
import { SectionHeader, useCollapsedSections } from "./CollapsibleSection";
import { InlineTitle } from "./InlineTitle";
import type { Chat } from "../lib/types";

interface ChatListProps {
  activeId: number | null;
  reloadKey: number;
  streamingIds: number[];
  /** Chat ids with a fired reminder, flagged with an alarm icon. */
  reminderChatIds?: Set<number>;
  onSelect: (chat: Chat) => void;
  /** Open the chat settings drawer (archive/delete/rename/promote live there). */
  onOpenSettings: (chat: Chat) => void;
  onChatsChanged?: () => void;
}

export function ChatList({
  activeId,
  reloadKey,
  streamingIds,
  reminderChatIds,
  onSelect,
  onOpenSettings,
  onChatsChanged,
}: ChatListProps) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [query, setQuery] = useState("");
  const { collapsed: collapsedSections, toggle: toggleSection } = useCollapsedSections(
    "precursor:chats:collapsedSections",
  );

  async function refresh(): Promise<void> {
    try {
      setChats(await api.listChats());
    } catch {
      setChats([]);
    }
  }

  useEffect(() => {
    void refresh();
  }, [reloadKey]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return chats;
    return chats.filter((c) => c.title.toLowerCase().includes(q));
  }, [chats, query]);

  const pinned = useMemo(() => filtered.filter((c) => c.pinned), [filtered]);
  const rest = useMemo(() => filtered.filter((c) => !c.pinned), [filtered]);

  async function renameChat(id: number, title: string): Promise<void> {
    await api.updateChat(id, { title });
    await refresh();
    onChatsChanged?.();
  }

  function renderItem(chat: Chat) {
    const isActive = chat.id === activeId;
    const isStreaming = streamingIds.includes(chat.id);
    return (
      <li key={chat.id}>
        <div
          role="button"
          tabIndex={0}
          onClick={() => onSelect(chat)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onSelect(chat);
            }
          }}
          className={`group flex items-center gap-2 rounded px-2 py-1.5 text-sm cursor-pointer ${
            isActive ? "bg-accent/15 text-accent" : "hover:bg-surface"
          }`}
        >
          {isStreaming ? (
            <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
          ) : (
            <MessageSquare size={14} className="shrink-0 opacity-70" />
          )}
          <InlineTitle
            title={chat.title}
            onRename={(t) => renameChat(chat.id, t)}
            className={`flex-1 truncate ${
              chat.unread_count > 0 || reminderChatIds?.has(chat.id) ? "font-semibold" : ""
            }`}
          />
          {reminderChatIds?.has(chat.id) && (
            <AlarmClock
              size={13}
              className="shrink-0 text-accent"
              aria-label="Reminder waiting"
            />
          )}
          {chat.unread_count > 0 && !isActive && (
            <span className="shrink-0 rounded-full bg-accent px-1.5 text-xs text-white">
              {chat.unread_count}
            </span>
          )}
          <button
            className="hidden shrink-0 rounded p-1 hover:bg-border group-hover:block"
            aria-label="Chat settings"
            data-tooltip="Chat settings"
            onClick={(e) => {
              e.stopPropagation();
              onOpenSettings(chat);
            }}
          >
            <Settings2 size={13} />
          </button>
        </div>
      </li>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search chats..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No chats yet.</div>
        ) : (
          <>
            {pinned.length > 0 && (
              <div className="mb-2">
                <SectionHeader
                  icon={<Pin size={11} />}
                  label="Pinned"
                  collapsed={collapsedSections.has("pinned")}
                  onToggle={() => toggleSection("pinned")}
                />
                {!collapsedSections.has("pinned") && (
                  <ul className="space-y-0.5">{pinned.map(renderItem)}</ul>
                )}
                <div className="mt-2 border-t border-border" />
              </div>
            )}
            <ul className="space-y-0.5">{rest.map(renderItem)}</ul>
          </>
        )}
      </div>
    </div>
  );
}
