import { useEffect, useMemo, useState } from "react";
import { Archive, Loader2, MessageSquare, Pin, Search, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { SectionHeader, useCollapsedSections } from "./CollapsibleSection";
import type { Chat } from "../lib/types";

interface ChatListProps {
  activeId: number | null;
  reloadKey: number;
  streamingIds: number[];
  onSelect: (chat: Chat) => void;
  onChatsChanged?: () => void;
}

export function ChatList({
  activeId,
  reloadKey,
  streamingIds,
  onSelect,
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

  async function handleArchive(e: React.MouseEvent, id: number): Promise<void> {
    e.stopPropagation();
    await api.archiveChat(id);
    await refresh();
    onChatsChanged?.();
  }

  async function handleDelete(e: React.MouseEvent, id: number): Promise<void> {
    e.stopPropagation();
    if (!window.confirm("Delete this chat and its transcript?")) return;
    await api.deleteChat(id);
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
          <span className="flex-1 truncate">{chat.title}</span>
          {chat.unread_count > 0 && !isActive && (
            <span className="shrink-0 rounded-full bg-accent px-1.5 text-xs text-white">
              {chat.unread_count}
            </span>
          )}
          <button
            className="hidden shrink-0 rounded p-1 hover:bg-border group-hover:block"
            aria-label="Archive chat"
            data-tooltip="Archive chat"
            onClick={(e) => void handleArchive(e, chat.id)}
          >
            <Archive size={13} />
          </button>
          <button
            className="hidden shrink-0 rounded p-1 hover:bg-border group-hover:block"
            aria-label="Delete chat"
            data-tooltip="Delete chat"
            onClick={(e) => void handleDelete(e, chat.id)}
          >
            <Trash2 size={13} />
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
