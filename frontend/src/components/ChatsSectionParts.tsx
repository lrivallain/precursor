import { Pin, PinOff, Settings as SettingsIcon } from "lucide-react";
import { ChatList } from "./ChatList";
import { ChatSessionPanel } from "./ChatSessionPanel";
import { ChatSettingsPanel } from "./ChatSettingsPanel";
import { InlineTitle } from "./InlineTitle";
import { ChatStartHero } from "./StartHero";
import type { Chat, Topic } from "../lib/types";
import type { ChatsController } from "../lib/useChatsController";

// The chats section's pieces of the app shell. Each one renders into a slot App
// owns (header, main pane, modal layer, sidebar, home launcher) and reads its
// state from the chats controller. Reminders, roles, streaming ids and the
// collection still come from App.

// The shared header's chats branch.
export function ChatsHeader({ controller }: { controller: ChatsController }) {
  const { activeChat, handleRenameChat, toggleChatPin, setChatSettingsOpen } = controller;
  return (
    <>
      {activeChat ? (
        <InlineTitle
          title={activeChat.title}
          onRename={(t) => handleRenameChat(activeChat.id, t)}
          className="truncate font-medium min-w-0 flex-1"
          inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
        />
      ) : (
        <span className="truncate font-medium min-w-0 flex-1">
          Select or create a chat
        </span>
      )}
      {activeChat && (
        <button
          className="p-2 rounded hover:bg-surface shrink-0"
          aria-label={activeChat.pinned ? "Unpin chat" : "Pin chat"}
          data-tooltip={activeChat.pinned ? "Unpin chat" : "Pin chat"}
          onClick={toggleChatPin}
        >
          {activeChat.pinned ? (
            <PinOff size={18} className="text-accent" />
          ) : (
            <Pin size={18} />
          )}
        </button>
      )}
      {activeChat && (
        <button
          className="p-2 rounded hover:bg-surface shrink-0"
          aria-label="Chat settings"
          data-tooltip="Chat settings"
          onClick={() => setChatSettingsOpen(true)}
        >
          <SettingsIcon size={18} />
        </button>
      )}
    </>
  );
}

// The main pane's chats branch: the open chat, or the start hero.
export function ChatsMain({
  controller,
  onRemindersChanged,
  onSetRole,
}: {
  controller: ChatsController;
  onRemindersChanged: () => void;
  onSetRole: (roleId: number | null) => Promise<void>;
}) {
  const {
    activeChat,
    activeChatReloadKey,
    refreshActiveChat,
    handleStartChat,
    setActiveChat,
    setChatListReloadKey,
  } = controller;
  return activeChat ? (
    <ChatSessionPanel
      key={`${activeChat.id}:${activeChatReloadKey}`}
      chat={activeChat}
      onChatUpdated={refreshActiveChat}
      onArchived={() => {
        setActiveChat(null);
        setChatListReloadKey((k) => k + 1);
      }}
      onRemindersChanged={onRemindersChanged}
      onSetRole={onSetRole}
    />
  ) : (
    <ChatStartHero onStart={handleStartChat} />
  );
}

// The home launcher's "New chat" card: the start composer, never a selection.
export function ChatsHomeSurface({ controller }: { controller: ChatsController }) {
  return <ChatStartHero onStart={controller.startChatFromHome} />;
}

// The chat settings drawer, opened from the header or a chat's list row.
// Promoting the chat to a topic hands off to App, which owns the topics state.
export function ChatsSettingsModal({
  controller,
  collectionId,
  onPromoted,
}: {
  controller: ChatsController;
  collectionId: number | null;
  onPromoted: (topic: Topic) => Promise<void>;
}) {
  const {
    activeChat,
    chatSettingsOpen,
    setChatSettingsOpen,
    setActiveChat,
    setChatListReloadKey,
    setActiveChatReloadKey,
  } = controller;
  if (!chatSettingsOpen || !activeChat) return null;
  return (
    <ChatSettingsPanel
      chat={activeChat}
      collectionId={collectionId}
      onClose={() => setChatSettingsOpen(false)}
      onSaved={(updated) => {
        setActiveChat(updated);
        setChatListReloadKey((k) => k + 1);
      }}
      onCleared={() => {
        setActiveChatReloadKey((k) => k + 1);
        setChatSettingsOpen(false);
      }}
      onArchived={() => {
        setActiveChat(null);
        setChatSettingsOpen(false);
        setChatListReloadKey((k) => k + 1);
      }}
      onDeleted={() => {
        setActiveChat(null);
        setChatSettingsOpen(false);
        setChatListReloadKey((k) => k + 1);
      }}
      onPromoted={async (topic) => {
        // The chat became a topic: leave Chats, switch to Topics, select it.
        setChatSettingsOpen(false);
        setActiveChat(null);
        setChatListReloadKey((k) => k + 1);
        await onPromoted(topic);
      }}
    />
  );
}

// The sidebar's chats slot. Named apart from `ChatList`, the list it wraps.
export function ChatsSidebarList({
  controller,
  streamingIds,
  reminderChatIds,
  onOpenReminder,
}: {
  controller: ChatsController;
  streamingIds: number[];
  reminderChatIds: Set<number>;
  onOpenReminder: (chat: Chat) => void;
}) {
  const {
    activeChat,
    chatListReloadKey,
    handleSelectChat,
    setActiveChat,
    setChatSettingsOpen,
    refreshActiveChat,
    setChatsUnread,
    handleArchiveChats,
    handleOpenChatNotes,
  } = controller;
  return (
    <ChatList
      activeId={activeChat?.id ?? null}
      reloadKey={chatListReloadKey}
      streamingIds={streamingIds}
      reminderChatIds={reminderChatIds}
      onSelect={handleSelectChat}
      onOpenSettings={(chat) => {
        setActiveChat(chat);
        setChatSettingsOpen(true);
      }}
      onChatsChanged={() => void refreshActiveChat()}
      onUnreadChange={setChatsUnread}
      onArchiveMany={handleArchiveChats}
      onOpenReminder={onOpenReminder}
      onOpenNotes={(chat) => void handleOpenChatNotes(chat)}
    />
  );
}
