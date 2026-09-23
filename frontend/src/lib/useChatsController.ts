import { useEffect, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { api } from "./api";
import { eventBus } from "./events";
import { openNotes } from "./notesOpen";
import { chatUrl, navigate, type AppRoute } from "./routes";
import { convKey, streamStore } from "./streamStore";
import type { Chat } from "./types";
import { windowFocused } from "./windowFocus";

// Shell state the chats section reads or drives. The once-registered listeners
// (App's `syncFromUrl`, stream completion and focus handler, the event bus
// below) keep the first render's controller, so everything they call only
// touches refs, state setters and the stable `isViewing`.
export interface ChatsControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  isViewing: (kind: "topic" | "chat" | "agent", id: number) => boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeMobileNav: () => void;
}

export interface ChatsController {
  activeChat: Chat | null;
  setActiveChat: Dispatch<SetStateAction<Chat | null>>;
  activeChatRef: RefObject<Chat | null>;
  chatListReloadKey: number;
  setChatListReloadKey: Dispatch<SetStateAction<number>>;
  activeChatReloadKey: number;
  setActiveChatReloadKey: Dispatch<SetStateAction<number>>;
  chatSettingsOpen: boolean;
  setChatSettingsOpen: Dispatch<SetStateAction<boolean>>;
  chatsUnread: number;
  setChatsUnread: Dispatch<SetStateAction<number>>;
  refreshChatsUnread: () => Promise<void>;
  handleSelectChat: (chat: Chat) => Promise<void>;
  selectChatById: (id: number) => Promise<void>;
  handleArchiveChats: (ids: number[]) => Promise<void>;
  refreshActiveChat: () => Promise<void>;
  toggleChatPin: () => Promise<void>;
  handleRenameChat: (id: number, title: string) => Promise<void>;
  handleStartChat: (prompt: string, roleId?: number | null) => Promise<void>;
  startChatFromHome: (prompt: string, roleId?: number | null) => Promise<void>;
  handleOpenChatNotes: (chat: Chat) => Promise<void>;
  handleStreamComplete: (chatId: number) => Promise<void>;
  setRoleForActive: (roleId: number | null) => Promise<void>;
  sectionUrl: () => string;
  syncFromRoute: (r: AppRoute) => void;
  startNew: () => void;
}

export function useChatsController(deps: ChatsControllerDeps): ChatsController {
  const {
    sidebarMode,
    atHome,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
  } = deps;

  const [activeChat, setActiveChat] = useState<Chat | null>(null);
  const [chatListReloadKey, setChatListReloadKey] = useState(0);
  const [activeChatReloadKey, setActiveChatReloadKey] = useState(0);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  // Total unread across chats, lifted from ChatList so the mode switcher can
  // badge the Chats tab even when that list isn't mounted.
  const [chatsUnread, setChatsUnread] = useState(0);

  // Mirror activeChat into a ref for the (registered-once) event subscription.
  const activeChatRef = useRef<Chat | null>(activeChat);
  useEffect(() => {
    activeChatRef.current = activeChat;
  }, [activeChat]);

  // Total chat unread, kept current in App (not just in ChatList) so the mode
  // switcher can badge the Chats tab from any mode. ChatList also reports its
  // own total via onUnreadChange for instant updates while it's mounted.
  async function refreshChatsUnread(): Promise<void> {
    try {
      const list = await api.chats.list();
      setChatsUnread(list.reduce((n, c) => n + (c.unread_count ?? 0), 0));
    } catch {
      // transient — keep the previous total
    }
  }

  // activeChat -> /chats/<slug>.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "chats" || !activeChat) return;
    const target = chatUrl(activeChat);
    if (window.location.pathname !== target) navigate(target);
  }, [activeChat, sidebarMode, atHome]);

  // Live sync across windows: chat changes arrive over the shared event bus.
  // `start()` is idempotent, so every controller starting it is harmless. The
  // topics controller's handler keeps the topic branches of the event types
  // both sections share; a chat id wins over a topic or agent id there.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
      if (event.type === "chat.changed") {
        // A chat's own metadata moved — most often auto-naming replacing the
        // placeholder title. Refresh the list, and the open chat if it's this one.
        setChatListReloadKey((k) => k + 1);
        const active = activeChatRef.current;
        if (active && (event.chat_id == null || event.chat_id === active.id)) {
          void (async () => {
            try {
              setActiveChat(await api.chats.get(active.id));
            } catch {
              // chat may have been deleted in another window; ignore
            }
          })();
        }
      } else if (event.type === "message.changed") {
        if (event.chat_id != null) {
          const chatId = event.chat_id;
          const isActive = isViewing("chat", chatId);
          // A chat turn changed — refresh the list badges and, if the user is
          // viewing it, remount the panel so it re-fetches from scratch. When
          // the change lands in the actively-viewed chat (e.g. a linked agent
          // posted into it), keep it read so its unread badge doesn't resurrect
          // when the user navigates away.
          void (async () => {
            if (isActive && windowFocused()) {
              try {
                await api.chats.markRead(chatId);
              } catch {
                // non-fatal
              }
            }
            setChatListReloadKey((k) => k + 1);
            void refreshChatsUnread();
          })();
          if (isActive) {
            setActiveChatReloadKey((k) => k + 1);
          }
        }
      } else if (event.type === "stream.started") {
        if (event.chat_id != null) {
          streamStore.setRemoteStreaming(convKey("chat", event.chat_id), true);
        }
      } else if (event.type === "stream.ended") {
        if (event.chat_id != null) {
          streamStore.setRemoteStreaming(convKey("chat", event.chat_id), false);
          setChatListReloadKey((k) => k + 1);
          void refreshChatsUnread();
        }
      } else if (event.type === "read.changed") {
        // Another tab marked a chat read. Refetch the chat unread state so this
        // tab's badge + counter clear in sync. This never re-marks anything, so
        // it can't loop with the active-view read logic.
        if (event.chat_id != null) {
          setChatListReloadKey((k) => k + 1);
          void refreshChatsUnread();
        }
      }
    });
    return () => {
      off();
    };
  }, []);

  // The chat branch of the stream completion (`useReadSync`): keep the chat
  // read if the user is watching it, then refresh the list badges.
  async function handleStreamComplete(id: number): Promise<void> {
    if (isViewing("chat", id) && windowFocused()) {
      try {
        await api.chats.markRead(id);
      } catch {
        // non-fatal
      }
    }
    setChatListReloadKey((k) => k + 1);
    void refreshChatsUnread();
  }

  async function handleOpenChatNotes(chat: Chat): Promise<void> {
    await handleSelectChat(chat);
    openNotes("chat", chat.id);
  }

  async function handleSelectChat(chat: Chat): Promise<void> {
    closeMobileNav();
    setActiveChat(chat);
    try {
      await api.chats.markRead(chat.id);
      setChatListReloadKey((k) => k + 1);
    } catch {
      // non-fatal
    }
  }

  // Select a chat known only by its id (a search hit, a fired reminder). A chat
  // deleted since then throws, and the caller decides what that means.
  async function selectChatById(id: number): Promise<void> {
    await handleSelectChat(await api.chats.get(id));
  }

  async function handleArchiveChats(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.chats.archive(id)));
    if (activeChat && ids.includes(activeChat.id)) setActiveChat(null);
    setChatListReloadKey((k) => k + 1);
  }

  // Re-fetch the active chat + nudge the list (after rename / pin / clear).
  async function refreshActiveChat(): Promise<void> {
    setChatListReloadKey((k) => k + 1);
    const active = activeChatRef.current;
    if (!active) return;
    try {
      setActiveChat(await api.chats.get(active.id));
    } catch {
      // chat may have been deleted elsewhere; ignore
    }
  }

  async function toggleChatPin(): Promise<void> {
    if (!activeChat) return;
    const updated = await api.chats.update(activeChat.id, { pinned: !activeChat.pinned });
    setActiveChat(updated);
    setChatListReloadKey((k) => k + 1);
  }

  async function handleRenameChat(id: number, title: string): Promise<void> {
    const updated = await api.chats.update(id, { title });
    if (activeChatRef.current?.id === id) setActiveChat(updated);
    setChatListReloadKey((k) => k + 1);
  }

  // Start hero: create a fresh chat and immediately send the user's first
  // prompt. Streaming is kicked off through the global store so it survives the
  // switch to the newly-mounted ChatSessionPanel. `autoname` marks the title as
  // a placeholder, so the backend replaces it with one derived from this prompt
  // while the answer is still streaming.
  async function handleStartChat(prompt: string, roleId: number | null = null): Promise<void> {
    const text = prompt.trim();
    if (!text) return;
    const chat = await api.chats.create({ title: "New chat", autoname: true, role_id: roleId });
    setActiveChat(chat);
    setChatListReloadKey((k) => k + 1);
    void streamStore.start(convKey("chat", chat.id), text);
  }

  // The "New chat" card's inline composer: create + stream, then reveal the chat.
  async function startChatFromHome(prompt: string, roleId: number | null = null): Promise<void> {
    setAtHome(false);
    setSidebarMode("chats");
    await handleStartChat(prompt, roleId);
  }

  // The chats branch of App's shared role persistence (`setRoleForActive`).
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (!activeChat) return;
    const updated = await api.chats.update(activeChat.id, { role_id: roleId });
    if (activeChatRef.current?.id === activeChat.id) setActiveChat(updated);
  }

  // Where section navigation lands: the open chat, else the start surface.
  function sectionUrl(): string {
    return activeChatRef.current ? chatUrl(activeChatRef.current) : "/chats";
  }

  // The chats branch of App's mount + back/forward URL sync. A bare `/chats`
  // leaves the open chat on screen: a known quirk (#344), kept as is.
  function syncFromRoute(r: AppRoute): void {
    const slug = r.chatSlug;
    if (!slug || activeChatRef.current?.slug === slug) return;
    void (async () => {
      try {
        const c = await api.chats.getBySlug(slug);
        setActiveChat(c);
        try {
          await api.chats.markRead(c.id);
          setChatListReloadKey((k) => k + 1);
        } catch {
          // non-fatal
        }
      } catch {
        // unknown slug — ignore
      }
    })();
  }

  // Drop the selection to reveal the chat start hero.
  function startNew(): void {
    setActiveChat(null);
    // Push rather than leave the chat's URL behind: a reload would reopen it,
    // and Back should return to the chat you were reading.
    if (window.location.pathname !== "/chats") navigate("/chats");
  }

  return {
    activeChat,
    setActiveChat,
    activeChatRef,
    chatListReloadKey,
    setChatListReloadKey,
    activeChatReloadKey,
    setActiveChatReloadKey,
    chatSettingsOpen,
    setChatSettingsOpen,
    chatsUnread,
    setChatsUnread,
    refreshChatsUnread,
    handleSelectChat,
    selectChatById,
    handleArchiveChats,
    refreshActiveChat,
    toggleChatPin,
    handleRenameChat,
    handleStartChat,
    startChatFromHome,
    handleOpenChatNotes,
    handleStreamComplete,
    setRoleForActive,
    sectionUrl,
    syncFromRoute,
    startNew,
  };
}
