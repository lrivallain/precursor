import { useEffect, useMemo } from "react";

const BASE_TITLE = "Precursor";

export interface UnreadTitleDeps {
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

// Returns the per-mode unread totals the section switchers badge.
export function useUnreadTitle(deps: UnreadTitleDeps): UnreadByMode {
  const { topicsUnread, chatsUnread, agentsUnread, agentsWaiting } = deps;

  // Reflect the total unread count in the tab title (always, independent of the
  // notification permission/setting). Cleared title falls back to the base.
  const unreadByMode = useMemo(
    () => ({ topics: topicsUnread, chats: chatsUnread, agents: agentsUnread }),
    [topicsUnread, chatsUnread, agentsUnread],
  );
  useEffect(() => {
    const n = topicsUnread + chatsUnread + agentsUnread;
    const bell = agentsWaiting > 0 ? "🔔 " : "";
    document.title = n > 0 ? `${bell}(${n}) ${BASE_TITLE}` : `${bell}${BASE_TITLE}`;
  }, [topicsUnread, chatsUnread, agentsUnread, agentsWaiting]);

  return unreadByMode;
}
