import { useEffect, useRef, useState } from "react";
import { FolderPlus, Pin, PinOff, Settings as SettingsIcon } from "lucide-react";
import { Sidebar, type SidebarMode } from "./components/Sidebar";
import { ChatPanel } from "./components/ChatPanel";
import { ChatList } from "./components/ChatList";
import { ChatSessionPanel } from "./components/ChatSessionPanel";
import { SettingsPanel } from "./components/SettingsPanel";
import { TopicCreateModal } from "./components/TopicCreateModal";
import { ScheduleModal } from "./components/ScheduleModal";
import { TopicSettingsPanel } from "./components/TopicSettingsPanel";
import { ArchivedTopicsPanel } from "./components/ArchivedTopicsPanel";
import { IssueStatusBadge } from "./components/IssueStatusBadge";
import { IssueLabelChip, IssueStateBadge } from "./components/IssueTags";
import {
  CreateWorkspaceModal,
  WorkspaceView,
} from "./components/WorkspaceView";
import { WorkspaceList } from "./components/WorkspaceList";
import { TooltipProvider } from "./components/Tooltip";
import { api } from "./lib/api";
import { eventBus } from "./lib/events";
import { notifyIfUnfocused } from "./lib/notifications";
import { skillsStore } from "./lib/skillsStore";
import { useSettings } from "./lib/settingsStore";
import { streamStore, useStreamVersion } from "./lib/streamStore";
import { useIssueContext } from "./lib/useIssueContext";
import type { Chat, Schedule, Topic, TopicNode, Workspace } from "./lib/types";

interface WsRoute {
  open: boolean;
  slug: string | null;
  path: string | null;
}

// Parse the current pathname into a workspace route. `/ws` opens the overlay,
// `/ws/<slug>/<file/path>` deep-links straight to a file.
function parseWsRoute(): WsRoute {
  const segs = window.location.pathname.replace(/^\/+|\/+$/g, "").split("/");
  if (segs[0] !== "ws") return { open: false, slug: null, path: null };
  const slug = segs[1] ? decodeURIComponent(segs[1]) : null;
  const path =
    segs.length > 2 ? segs.slice(2).map(decodeURIComponent).join("/") : null;
  return { open: true, slug, path };
}

const BASE_TITLE = "Precursor";

/** Sum unread counts across the whole topic tree (recursively). */
function totalUnread(nodes: TopicNode[]): number {
  let n = 0;
  for (const node of nodes) {
    n += node.unread_count ?? 0;
    if (node.children?.length) n += totalUnread(node.children);
  }
  return n;
}

/** Find a topic's title anywhere in the tree (for notification text). */
function findTitle(nodes: TopicNode[], topicId: number): string | null {
  for (const node of nodes) {
    if (node.id === topicId) return node.title;
    if (node.children?.length) {
      const hit = findTitle(node.children, topicId);
      if (hit) return hit;
    }
  }
  return null;
}


export default function App() {
  const [tree, setTree] = useState<TopicNode[]>([]);
  const [activeTopic, setActiveTopic] = useState<Topic | null>(null);
  // The active sidebar mode. Workspaces own the `/ws` URL, so a deep link
  // (or reload onto `/ws/...`) starts the app directly in workspaces mode.
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>(() =>
    parseWsRoute().open ? "workspaces" : "topics",
  );
  const [activeChat, setActiveChat] = useState<Chat | null>(null);
  const [chatListReloadKey, setChatListReloadKey] = useState(0);
  const [globalSettingsOpen, setGlobalSettingsOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [wsRoute, setWsRoute] = useState<WsRoute>(parseWsRoute);
  // Workspaces are loaded lazily when the user first enters workspaces mode.
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<number | null>(null);
  const [createWorkspaceOpen, setCreateWorkspaceOpen] = useState(false);
  const [topicSettingsOpen, setTopicSettingsOpen] = useState(false);
  const [topicSettingsTab, setTopicSettingsTab] = useState<"settings" | "context">(
    "settings",
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [createParentId, setCreateParentId] = useState<number | null | undefined>(undefined);
  const [chatReloadKey, setChatReloadKey] = useState(0);
  // Schedule editor: undefined = closed, null = creating, Schedule = editing.
  const [scheduleModal, setScheduleModal] = useState<Schedule | null | undefined>(
    undefined,
  );

  useStreamVersion();
  const streamingTopicIds = streamStore.streamingTopicIds();

  const settings = useSettings();
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;

  const issueContext = useIssueContext(activeTopic, setActiveTopic);

  // Mirror activeTopic into a ref so the onComplete callback (set up once)
  // can read the current value without resubscribing on every change.
  const activeTopicRef = useRef<Topic | null>(activeTopic);
  useEffect(() => {
    activeTopicRef.current = activeTopic;
  }, [activeTopic]);

  // Mirror activeChat into a ref for the (registered-once) event subscription.
  const activeChatRef = useRef<Chat | null>(activeChat);
  useEffect(() => {
    activeChatRef.current = activeChat;
  }, [activeChat]);

  // Mirror the workspaces overlay state so the topic-hash effect can bail out
  // without re-subscribing whenever the overlay toggles.
  const workspacesOpenRef = useRef(wsRoute.open);
  useEffect(() => {
    workspacesOpenRef.current = wsRoute.open;
  }, [wsRoute.open]);

  async function refreshTree(): Promise<void> {
    setTree(await api.topicTree());
  }

  useEffect(() => {
    void refreshTree();
    void skillsStore.load();
  }, []);

  // Reflect the unread count in the tab title (always, independent of the
  // notification permission/setting). Cleared title falls back to the base.
  useEffect(() => {
    const n = totalUnread(tree);
    document.title = n > 0 ? `(${n}) ${BASE_TITLE}` : BASE_TITLE;
  }, [tree]);

  // Mirror tree + notification setting into refs so the completion callbacks
  // (registered once) read current values without re-subscribing.
  const treeRef = useRef<TopicNode[]>(tree);
  useEffect(() => {
    treeRef.current = tree;
  }, [tree]);
  const notificationsEnabledRef = useRef(false);
  useEffect(() => {
    notificationsEnabledRef.current = settings?.notifications_enabled ?? false;
  }, [settings]);

  // Fire a browser notification for a completed turn, when enabled and the
  // window isn't focused. Skips the topic the user is actively viewing.
  function maybeNotify(topicId: number): void {
    if (!notificationsEnabledRef.current) return;
    if (activeTopicRef.current?.id === topicId && document.hasFocus()) return;
    const title = findTitle(treeRef.current, topicId) ?? "Precursor";
    notifyIfUnfocused({
      title,
      body: "A new reply is ready.",
      tag: `precursor-topic-${topicId}`,
    });
  }

  // ---- URL hash routing ------------------------------------------------
  // `#<slug>` is the canonical deep link for a topic. We keep it in sync
  // both ways: clicking a topic updates the hash, and back/forward (or a
  // pasted link) resolves the slug back to an active topic. The equality
  // checks below break the feedback loop between the two effects.

  // hash -> activeTopic (initial mount + back/forward + pasted links).
  useEffect(() => {
    const sync = (): void => {
      const slug = window.location.hash.replace(/^#/, "").trim();
      if (!slug) return;
      if (activeTopicRef.current?.slug === slug) return;
      void (async () => {
        try {
          const t = await api.getTopicBySlug(slug);
          setActiveTopic(t);
          try {
            await api.markTopicRead(t.id);
            await refreshTree();
          } catch {
            // non-fatal
          }
        } catch {
          // unknown slug — leave the user where they are
        }
      })();
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  // activeTopic -> hash. Use replaceState when there's no prior hash so the
  // initial selection doesn't leave a junk history entry; use a real
  // assignment otherwise so back/forward walks through topics.
  useEffect(() => {
    if (workspacesOpenRef.current) return; // Workspaces own the URL while open
    if (activeTopic) {
      const target = `#${activeTopic.slug}`;
      if (window.location.hash !== target) {
        if (!window.location.hash) {
          history.replaceState(
            null,
            "",
            `${window.location.pathname}${window.location.search}${target}`,
          );
        } else {
          window.location.hash = target;
        }
      }
    } else if (window.location.hash) {
      history.replaceState(
        null,
        "",
        `${window.location.pathname}${window.location.search}`,
      );
    }
  }, [activeTopic]);

  // ---- /ws route --------------------------------------------------------
  // Workspaces are a sidebar mode that owns a real path (not a hash, which
  // topic deep links already use) so they survive reloads / back-forward and
  // can be deep-linked down to a file. The path drives both the route state
  // and the active sidebar mode.
  useEffect(() => {
    const sync = (): void => {
      const r = parseWsRoute();
      setWsRoute(r);
      // Back/forward onto (or away from) /ws flips the sidebar mode to match.
      setSidebarMode((m) => (r.open ? "workspaces" : m === "workspaces" ? "topics" : m));
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  // Switch sidebar mode, keeping the URL in sync: entering Workspaces pushes
  // `/ws`; leaving it restores the root path (+ the active topic hash).
  function changeMode(next: SidebarMode): void {
    if (next === sidebarMode) return;
    if (next === "workspaces") {
      if (!parseWsRoute().open) history.pushState(null, "", "/ws");
      setWsRoute(parseWsRoute());
    } else if (sidebarMode === "workspaces") {
      const hash = activeTopicRef.current ? `#${activeTopicRef.current.slug}` : "";
      history.pushState(null, "", `/${hash}`);
      setWsRoute({ open: false, slug: null, path: null });
    }
    setSidebarMode(next);
  }

  // Reflect the active workspace + open file in the URL so a reload returns to
  // the same place. replaceState keeps it as a single history entry.
  function navigateWorkspace(slug: string | null, filePath: string | null): void {
    let url = "/ws";
    if (slug) {
      url += `/${encodeURIComponent(slug)}`;
      if (filePath) {
        url += "/" + filePath.split("/").map(encodeURIComponent).join("/");
      }
    }
    history.replaceState(null, "", url);
    setWsRoute({ open: true, slug, path: filePath });
  }


  // Whenever any stream finishes, refresh the tree so unread badges and
  // updated_at timestamps reflect the new server state. If the user happens to
  // be viewing the topic that just finished, also mark it read.
  useEffect(() => {
    streamStore.setOnComplete((topicId) => {
      void (async () => {
        if (activeTopicRef.current?.id === topicId) {
          try {
            await api.markTopicRead(topicId);
          } catch {
            // non-fatal
          }
        }
        await refreshTree();
        // Foreground turn finished — notify if the user has switched away.
        maybeNotify(topicId);
      })();
    });
    return () => streamStore.setOnComplete(null);
  }, []);

  // Live sync across windows: any mutation in another tab/process pushes an
  // event over /api/events. Echoes (events tagged with our own client id)
  // are filtered out inside the bus.
  useEffect(() => {
    eventBus.start();
    return eventBus.subscribe((event) => {
      if (event.type === "topic.changed") {
        void refreshTree();
        const active = activeTopicRef.current;
        if (active && (event.topic_id === null || event.topic_id === active.id)) {
          void (async () => {
            try {
              const refreshed = await api.getTopic(active.id);
              setActiveTopic(refreshed);
            } catch {
              // topic may have been deleted in another window; ignore
            }
          })();
        }
      } else if (event.type === "message.changed") {
        if (event.chat_id != null) {
          // A chat turn changed — refresh the chat list (badges) and, if the
          // user is viewing it, the panel reloads via its own activity hook.
          setChatListReloadKey((k) => k + 1);
          return;
        }
        // Sidebar badge tracking depends on the tree, so always refresh.
        void refreshTree();
        if (activeTopicRef.current?.id === event.topic_id) {
          // Re-mount ChatPanel so it re-fetches messages from scratch.
          setChatReloadKey((k) => k + 1);
        }
      } else if (event.type === "stream.started") {
        if (event.topic_id != null) streamStore.setRemoteStreaming(event.topic_id, true);
      } else if (event.type === "stream.ended") {
        if (event.chat_id != null) {
          setChatListReloadKey((k) => k + 1);
          return;
        }
        if (event.topic_id != null) {
          streamStore.setRemoteStreaming(event.topic_id, false);
          // A turn finished elsewhere (another window or a scheduled task). The
          // driving window's own echo is filtered by client id, so this only
          // covers background completions — notify if enabled + unfocused.
          maybeNotify(event.topic_id);
        }
      }
    });
  }, []);

  async function handleSelect(id: number): Promise<void> {
    setActiveTopic(await api.getTopic(id));
    try {
      await api.markTopicRead(id);
      await refreshTree();
    } catch {
      // non-fatal
    }
  }

  async function handleSelectChat(chat: Chat): Promise<void> {
    setActiveChat(chat);
    try {
      await api.markChatRead(chat.id);
      setChatListReloadKey((k) => k + 1);
    } catch {
      // non-fatal
    }
  }

  // ---- Workspaces -------------------------------------------------------
  const activeWorkspace =
    workspaces?.find((w) => w.id === activeWorkspaceId) ?? null;
  // The route path only applies to the workspace named in the URL.
  const workspaceInitialPath =
    activeWorkspace && activeWorkspace.slug === wsRoute.slug ? wsRoute.path : null;

  async function loadWorkspaces(): Promise<Workspace[]> {
    const list = await api.listWorkspaces();
    setWorkspaces(list);
    return list;
  }

  // Lazily load workspaces the first time the user enters that mode, then pick
  // an active one (honouring a slug from the URL, else the first).
  useEffect(() => {
    if (sidebarMode !== "workspaces" || workspaces !== null) return;
    void loadWorkspaces().then((list) => {
      if (list.length === 0) return;
      const fromRoute = wsRoute.slug
        ? list.find((w) => w.slug === wsRoute.slug)
        : undefined;
      setActiveWorkspaceId((id) => id ?? fromRoute?.id ?? list[0].id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode]);

  function handleSelectWorkspace(ws: Workspace): void {
    setActiveWorkspaceId(ws.id);
    navigateWorkspace(ws.slug, null);
  }

  function handleCreate(parentId: number | null): void {
    // Top-level "+ create" passes null; in that case, if a topic is currently
    // selected, nest the new one under it. Per-node "+ child" buttons pass
    // their own id explicitly and are left alone.
    setCreateParentId(parentId ?? activeTopic?.id ?? null);
  }

  async function handleEditSchedule(topicId: number): Promise<void> {
    try {
      setScheduleModal(await api.getSchedule(topicId));
    } catch {
      // schedule may have been deleted elsewhere; ignore
    }
  }

  function openTopicSettings(tab: "settings" | "context" = "settings"): void {
    setTopicSettingsTab(tab);
    setTopicSettingsOpen(true);
  }

  async function togglePin(): Promise<void> {
    if (!activeTopic) return;
    const updated = await api.updateTopic(activeTopic.id, {
      pinned: !activeTopic.pinned,
    });
    setActiveTopic(updated);
    await refreshTree();
  }

  return (
    <div className="flex h-full w-full bg-bg text-text">
      <TooltipProvider />
      <Sidebar
        tree={tree}
        activeId={activeTopic?.id ?? null}
        streamingTopicIds={streamingTopicIds}
        collapsed={sidebarCollapsed}
        mode={sidebarMode}
        onModeChange={changeMode}
        chatSlot={
          <ChatList
            activeId={activeChat?.id ?? null}
            reloadKey={chatListReloadKey}
            onSelect={handleSelectChat}
            onChatsChanged={() => setChatListReloadKey((k) => k + 1)}
          />
        }
        workspaceSlot={
          <WorkspaceList
            workspaces={workspaces}
            activeId={activeWorkspaceId}
            onSelect={handleSelectWorkspace}
            onCreate={() => setCreateWorkspaceOpen(true)}
          />
        }
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        onSelect={handleSelect}
        onCreate={handleCreate}
        onCreateSchedule={() => setScheduleModal(null)}
        onEditSchedule={handleEditSchedule}
        onRefresh={refreshTree}
        onOpenGlobalSettings={() => setGlobalSettingsOpen(true)}
        onOpenArchive={() => setArchiveOpen(true)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        {/* One shared header across every mode: active item title on the left,
            mode-specific actions on the right. */}
        <header className="flex items-center justify-between px-4 h-12 border-b border-border gap-3">
          {sidebarMode === "topics" ? (
            <>
              <div className="flex items-center gap-2 min-w-0 flex-1">
                <span className="truncate font-medium">
                  {activeTopic ? activeTopic.title : "Select or create a topic"}
                </span>
                {activeTopic && issueAssociationsEnabled && activeTopic.kind !== "scheduled" && (
                  <IssueStatusBadge
                    status={issueContext.status}
                    onClick={() => openTopicSettings("context")}
                  />
                )}
              </div>
              {activeTopic &&
                issueAssociationsEnabled &&
                activeTopic.kind !== "scheduled" &&
                issueContext.summary && (
                  <div className="flex items-center gap-1.5 flex-wrap justify-end min-w-0">
                    <IssueStateBadge state={issueContext.summary.issue_state} />
                    {issueContext.summary.labels.map((label) => (
                      <IssueLabelChip key={label.name} label={label} />
                    ))}
                  </div>
                )}
              {activeTopic && (
                <button
                  className="p-2 rounded hover:bg-surface shrink-0"
                  aria-label={activeTopic.pinned ? "Unpin topic" : "Pin topic"}
                  data-tooltip={activeTopic.pinned ? "Unpin topic" : "Pin topic"}
                  onClick={togglePin}
                >
                  {activeTopic.pinned ? (
                    <PinOff size={18} className="text-accent" />
                  ) : (
                    <Pin size={18} />
                  )}
                </button>
              )}
              {activeTopic && (
                <button
                  className="p-2 rounded hover:bg-surface shrink-0"
                  aria-label="Topic settings"
                  data-tooltip="Topic settings"
                  onClick={() => openTopicSettings("settings")}
                >
                  <SettingsIcon size={18} />
                </button>
              )}
            </>
          ) : sidebarMode === "chats" ? (
            <span className="truncate font-medium min-w-0 flex-1">
              {activeChat ? activeChat.title : "Select or create a chat"}
            </span>
          ) : (
            <>
              <span className="truncate font-medium min-w-0 flex-1">
                {activeWorkspace ? activeWorkspace.name : "Workspaces"}
              </span>
              <button
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 shrink-0"
                onClick={() => setCreateWorkspaceOpen(true)}
              >
                <FolderPlus size={16} /> New workspace
              </button>
            </>
          )}
        </header>

        <div className="flex-1 min-h-0">
          {sidebarMode === "topics" ? (
            activeTopic ? (
              <ChatPanel
                key={`${activeTopic.id}:${chatReloadKey}`}
                topic={activeTopic}
                onTopicUpdated={async () => {
                  // Commands persist messages server-side; clear the badge
                  // for the topic the user is actively viewing before
                  // re-loading the tree so its unread count doesn't tick up.
                  // Also re-fetch the active topic itself — some commands
                  // (e.g. /gh-create) mutate topic fields like the linked
                  // issue number, and the chat header needs to reflect that.
                  try {
                    await api.markTopicRead(activeTopic.id);
                  } catch {
                    // non-fatal
                  }
                  try {
                    setActiveTopic(await api.getTopic(activeTopic.id));
                  } catch {
                    // non-fatal
                  }
                  await refreshTree();
                }}
                onArchived={async () => {
                  // /archive removes the topic from the active view; drop the
                  // selection and refresh the tree so it moves to the archive.
                  setActiveTopic(null);
                  await refreshTree();
                }}
                onNavigateTopic={async (topic) => {
                  // /new created a child topic — switch to it and refresh so it
                  // appears in the tree.
                  setActiveTopic(topic);
                  await refreshTree();
                }}
              />
            ) : (
              <EmptyHero label="No topic selected." />
            )
          ) : sidebarMode === "chats" ? (
            activeChat ? (
              <ChatSessionPanel
                key={activeChat.id}
                chat={activeChat}
                onActivity={() => setChatListReloadKey((k) => k + 1)}
              />
            ) : (
              <EmptyHero label="No chat selected." />
            )
          ) : workspaces === null ? (
            <EmptyHero label="Loading workspaces…" />
          ) : activeWorkspace ? (
            <WorkspaceView
              key={activeWorkspace.id}
              workspace={activeWorkspace}
              initialPath={workspaceInitialPath}
              onPathChange={(p) => navigateWorkspace(activeWorkspace.slug, p)}
              onDeleted={async () => {
                const list = await loadWorkspaces();
                const next = list[0] ?? null;
                setActiveWorkspaceId(next?.id ?? null);
                navigateWorkspace(next?.slug ?? null, null);
              }}
            />
          ) : (
            <EmptyHero label="No workspaces yet." />
          )}
        </div>
      </main>

      {globalSettingsOpen && (
        <SettingsPanel onClose={() => setGlobalSettingsOpen(false)} />
      )}

      {createWorkspaceOpen && (
        <CreateWorkspaceModal
          onClose={() => setCreateWorkspaceOpen(false)}
          onCreated={async (workspace) => {
            setCreateWorkspaceOpen(false);
            await loadWorkspaces();
            setActiveWorkspaceId(workspace.id);
            navigateWorkspace(workspace.slug, null);
          }}
        />
      )}

      {archiveOpen && (
        <ArchivedTopicsPanel
          onClose={() => setArchiveOpen(false)}
          onRestored={async () => {
            await refreshTree();
          }}
          onDeleted={async (id) => {
            if (activeTopic?.id === id) setActiveTopic(null);
            await refreshTree();
          }}
        />
      )}

      {topicSettingsOpen && activeTopic && (
        <TopicSettingsPanel
          topic={activeTopic}
          tree={tree}
          context={issueContext}
          initialTab={topicSettingsTab}
          onClose={() => setTopicSettingsOpen(false)}
          onSaved={async (updated) => {
            setActiveTopic(updated);
            setTopicSettingsOpen(false);
            await refreshTree();
          }}
          onDeleted={async () => {
            setTopicSettingsOpen(false);
            setActiveTopic(null);
            await refreshTree();
          }}
          onCleared={() => {
            setChatReloadKey((k) => k + 1);
            setTopicSettingsOpen(false);
          }}
        />
      )}

      {createParentId !== undefined && (
        <TopicCreateModal
          initialParentId={createParentId}
          tree={tree}
          onClose={() => setCreateParentId(undefined)}
          onCreated={async (topic) => {
            setCreateParentId(undefined);
            await refreshTree();
            setActiveTopic(topic);
          }}
        />
      )}

      {scheduleModal !== undefined && (
        <ScheduleModal
          schedule={scheduleModal}
          onClose={() => setScheduleModal(undefined)}
          onSaved={async () => {
            setScheduleModal(undefined);
            await refreshTree();
          }}
        />
      )}
    </div>
  );
}

/** Centered logo + caption shown when a mode has nothing selected. */
function EmptyHero({ label }: { label: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-muted gap-3">
      <img
        src="/logo.svg"
        alt=""
        aria-hidden="true"
        width={72}
        height={72}
        className="rounded-2xl opacity-90"
      />
      <span>{label}</span>
    </div>
  );
}

