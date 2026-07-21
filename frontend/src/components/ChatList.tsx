import { useEffect, useMemo, useState } from "react";
import { AlarmClock, Loader2, MessageSquare, Pin, Search, Settings2 } from "lucide-react";
import { api } from "../lib/api";
import { SectionHeader, useCollapsedSections } from "./CollapsibleSection";
import { InlineTitle } from "./InlineTitle";
import type { Chat } from "../lib/types";
import { useMultiSelect } from "../lib/useMultiSelect";
import { useScrollActiveIntoView } from "../lib/useScrollActiveIntoView";
import { SelectToggleButton, SelectionToolbar, SelectionCheckbox } from "./ListSelection";

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
  /** Reports the total unread count across chats after each refresh. */
  onUnreadChange?: (total: number) => void;
  /** Bulk-archive the selected chats. Enables multi-select when provided. */
  onArchiveMany?: (ids: number[]) => void | Promise<void>;
}

export function ChatList({
  activeId,
  reloadKey,
  streamingIds,
  reminderChatIds,
  onSelect,
  onOpenSettings,
  onChatsChanged,
  onUnreadChange,
  onArchiveMany,
}: ChatListProps) {
  const [chats, setChats] = useState<Chat[]>([]);
  const [query, setQuery] = useState("");
  const sel = useMultiSelect();
  const [busy, setBusy] = useState(false);
  const activeItemRef = useScrollActiveIntoView<HTMLDivElement>(activeId);
  const { collapsed: collapsedSections, toggle: toggleSection } = useCollapsedSections(
    "precursor:chats:collapsedSections",
  );

  async function refresh(): Promise<void> {
    try {
      const list = await api.chats.list();
      setChats(list);
      onUnreadChange?.(list.reduce((n, c) => n + (c.unread_count ?? 0), 0));
    } catch {
      setChats([]);
      onUnreadChange?.(0);
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

  const filteredIds = useMemo(() => filtered.map((c) => c.id), [filtered]);
  useEffect(() => {
    if (sel.active) sel.prune(filteredIds);
  }, [filteredIds, sel]);

  const allSelected = filteredIds.length > 0 && filteredIds.every((id) => sel.isSelected(id));

  async function archiveSelected(): Promise<void> {
    if (!onArchiveMany || sel.count === 0) return;
    setBusy(true);
    try {
      await onArchiveMany([...sel.selected]);
      sel.exit();
      await refresh();
      onChatsChanged?.();
    } finally {
      setBusy(false);
    }
  }

  async function renameChat(id: number, title: string): Promise<void> {
    await api.chats.update(id, { title });
    await refresh();
    onChatsChanged?.();
  }

  function renderItem(chat: Chat) {
    const isActive = chat.id === activeId;
    const isStreaming = streamingIds.includes(chat.id);
    const selected = sel.isSelected(chat.id);
    return (
      <li key={chat.id}>
        <div
          ref={isActive && !sel.active ? activeItemRef : undefined}
          role="button"
          tabIndex={0}
          onClick={() => (sel.active ? sel.toggle(chat.id) : onSelect(chat))}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              if (sel.active) sel.toggle(chat.id);
              else onSelect(chat);
            }
          }}
          className={`group flex items-center gap-2 rounded px-2 py-1.5 text-sm cursor-pointer ${
            sel.active && selected
              ? "bg-accent/15"
              : isActive && !sel.active
                ? "section-selected"
                : "hover:bg-surface"
          }`}
        >
          {sel.active ? (
            <SelectionCheckbox checked={selected} />
          ) : isStreaming ? (
            <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
          ) : (
            <MessageSquare size={14} className="shrink-0 opacity-70" />
          )}
          {sel.active ? (
            <span
              className={`flex-1 truncate ${
                chat.unread_count > 0 || reminderChatIds?.has(chat.id) ? "font-semibold" : ""
              }`}
            >
              {chat.title}
            </span>
          ) : (
            <InlineTitle
              title={chat.title}
              onRename={(t) => renameChat(chat.id, t)}
              className={`flex-1 truncate ${
                chat.unread_count > 0 || reminderChatIds?.has(chat.id) ? "font-semibold" : ""
              }`}
            />
          )}
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
          {!sel.active && (
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
          )}
        </div>
      </li>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
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
          {onArchiveMany && !sel.active && filtered.length > 0 && (
            <SelectToggleButton onClick={() => sel.enter()} />
          )}
        </div>
      </div>

      {sel.active && (
        <SelectionToolbar
          count={sel.count}
          allSelected={allSelected}
          onToggleAll={() => sel.toggleAll(filteredIds)}
          onArchive={() => void archiveSelected()}
          onCancel={() => sel.exit()}
          busy={busy}
        />
      )}

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
