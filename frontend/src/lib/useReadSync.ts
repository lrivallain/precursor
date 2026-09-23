import { useEffect } from "react";
import { api } from "./api";
import { streamStore } from "./streamStore";
import type { AgentsController } from "./useAgentsController";
import type { ChatsController } from "./useChatsController";
import type { TopicsController } from "./useTopicsController";

// Both effects below register once, so they keep the first render's deps:
// `currentlyViewed` must be stable, and the controller actions they call only
// touch refs and state setters.
export interface ReadSyncDeps {
  currentlyViewed: () => { kind: "topic" | "chat" | "agent"; id: number } | null;
  topics: TopicsController;
  chats: ChatsController;
  agents: AgentsController;
}

// Keeps read state in step with what's on screen: the single stream-completion
// callback (the stream store takes only one) and the mark-read on refocus.
export function useReadSync(deps: ReadSyncDeps): void {
  const { currentlyViewed, topics, chats, agents } = deps;

  // Whenever any stream finishes, refresh the tree so unread badges and
  // updated_at timestamps reflect the new server state. If the user happens to
  // be viewing the topic that just finished, also mark it read.
  useEffect(() => {
    streamStore.setOnComplete((key) => {
      const [kind, rawId] = key.split(":");
      const id = Number(rawId);
      void (async () => {
        if (kind === "chat") {
          await chats.handleStreamComplete(id);
          return;
        }
        await topics.handleStreamComplete(id);
      })();
    });
    return () => streamStore.setOnComplete(null);
  }, []);

  // When this tab regains focus, mark whatever conversation it's showing read.
  // This is the complement to the focus-gated auto-marks: unread that piled up
  // while the tab was backgrounded clears the moment the user looks at it again,
  // and the read.changed broadcast keeps other tabs in sync. We listen for both
  // window focus (switching OS windows) and visibility (switching browser tabs).
  useEffect(() => {
    function markActiveRead(): void {
      const v = currentlyViewed();
      if (v == null) return;
      void (async () => {
        try {
          if (v.kind === "chat") {
            await api.chats.markRead(v.id);
            chats.setChatListReloadKey((k) => k + 1);
            void chats.refreshChatsUnread();
          } else if (v.kind === "topic") {
            await api.topics.markRead(v.id);
            await topics.refreshTree();
          } else {
            await api.agents.markRead(v.id);
            await agents.loadAgents();
          }
        } catch {
          // non-fatal
        }
      })();
    }
    function onVisibility(): void {
      if (document.visibilityState === "visible") markActiveRead();
    }
    window.addEventListener("focus", markActiveRead);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", markActiveRead);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
}
