import { useEffect, useMemo } from "react";

export interface DocumentTitleDeps {
  /** What is on screen — see `pageTitle` in lib/pageTitle.ts. */
  page: string;
  topicsUnread: number;
  chatsUnread: number;
  agentsUnread: number;
  agentsWaiting: number;
}

// A type alias, not an interface: the section switchers take it as a
// `Partial<Record<SidebarMode, number>>`, which needs the implicit index
// signature only object literal types get.
export type UnreadByMode = {
  topics: number;
  chats: number;
  agents: number;
};

// Keeps the tab title on the open item and section, prefixed with the unread
// total. Returns the per-mode unread totals the section switchers badge.
//
// Call it after every hook whose effects navigate: a history entry takes the
// title the page has while it is current, so retitling ahead of a pushState in
// the same commit would label the entry being left with the new page's name.
export function useDocumentTitle(deps: DocumentTitleDeps): UnreadByMode {
  const { page, topicsUnread, chatsUnread, agentsUnread, agentsWaiting } = deps;

  const unreadByMode = useMemo(
    () => ({ topics: topicsUnread, chats: chatsUnread, agents: agentsUnread }),
    [topicsUnread, chatsUnread, agentsUnread],
  );
  // The unread count shows regardless of the notification permission/setting.
  useEffect(() => {
    const n = topicsUnread + chatsUnread + agentsUnread;
    const bell = agentsWaiting > 0 ? "🔔 " : "";
    const count = n > 0 ? `(${n}) ` : "";
    document.title = `${bell}${count}${page}`;
  }, [page, topicsUnread, chatsUnread, agentsUnread, agentsWaiting]);

  return unreadByMode;
}
