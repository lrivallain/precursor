import { useEffect, useMemo, useState } from "react";
import { Archive, MessageSquare, Plus, Search, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Chat } from "../lib/types";

interface ChatListProps {
  activeId: number | null;
  reloadKey: number;
  onSelect: (chat: Chat) => void;
  onChatsChanged?: () => void;
}

export function ChatList({ activeId, reloadKey, onSelect, onChatsChanged }: ChatListProps) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);

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

  async function handleCreate(): Promise<void> {
    if (creating) return;
    setCreating(true);
    try {
      const chat = await api.createChat({ title: "New chat" });
      await refresh();
      onSelect(chat);
      onChatsChanged?.();
    } finally {
      setCreating(false);
    }
  }

  async function handleArchive(e: React.MouseEvent, id: number): Promise<void> {
    e.stopPropagation();
    await api.archiveChat(id);
    await refresh();
    onChatsChanged?.();
  }

  async function handleDelete(e: React.MouseEvent, id: number): Promise<void> {
    e.stopPropagation();
    await api.deleteChat(id);
    await refresh();
    onChatsChanged?.();
  }

  function renderItem(chat: Chat) {
    const isActive = chat.id === activeId;
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
          <MessageSquare size={14} className="shrink-0 opacity-70" />
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
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search chats..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
        <button
          className="rounded p-1.5 hover:bg-surface"
          aria-label="New chat"
          data-tooltip="New chat"
          onClick={() => void handleCreate()}
        >
          <Plus size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No chats yet.</div>
        ) : (
          <>
            {pinned.length > 0 && (
              <ul className="mb-2 space-y-0.5">{pinned.map(renderItem)}</ul>
            )}
            <ul className="space-y-0.5">{rest.map(renderItem)}</ul>
          </>
        )}
      </div>
    </div>
  );
}
