import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  MessagesSquare,
  Pin,
  PinOff,
  Settings as SettingsIcon,
  Square,
  Trash2,
} from "lucide-react";
import { Sidebar, type SidebarMode } from "./components/Sidebar";
import { ChatPanel } from "./components/ChatPanel";
import { ChatList } from "./components/ChatList";
import { ChatSessionPanel } from "./components/ChatSessionPanel";
import { ChatSettingsPanel } from "./components/ChatSettingsPanel";
import { McpAuthBanner } from "./components/McpAuthBanner";
import { SettingsPanel } from "./components/SettingsPanel";
import { TopicCreateModal } from "./components/TopicCreateModal";
import { ScheduleModal } from "./components/ScheduleModal";
import { TopicSettingsPanel } from "./components/TopicSettingsPanel";
import { ArchivePanel } from "./components/ArchivePanel";
import { IssueStatusBadge } from "./components/IssueStatusBadge";
import { IssueLabelChip, IssueStateBadge } from "./components/IssueTags";
import {
  CreateWorkspaceModal,
  WorkspaceView,
} from "./components/WorkspaceView";
import { WorkspaceList } from "./components/WorkspaceList";
import { AgentList } from "./components/AgentList";
import { AgentSettingsPanel } from "./components/AgentSettingsPanel";
import { AgentStatusBadge } from "./components/AgentStatusBadge";
import { AgentView } from "./components/AgentView";
import { DetachedDraftHost } from "./components/DetachedDraftHost";
import { InlineTitle } from "./components/InlineTitle";
import { useConfirm } from "./components/ConfirmDialog";
import { RoleSelector } from "./components/RoleSelector";
import { TooltipProvider } from "./components/Tooltip";
import { api } from "./lib/api";
import { eventBus } from "./lib/events";
import { notifyIfUnfocused } from "./lib/notifications";
import { skillsStore } from "./lib/skillsStore";
import { rolesStore } from "./lib/rolesStore";
import { useSettings } from "./lib/settingsStore";
import { streamStore, useStreamVersion, convKey } from "./lib/streamStore";
import { useIssueContext } from "./lib/useIssueContext";
import type {
  AgentSession,
  Chat,
  ReminderItem,
  Schedule,
  Topic,
  TopicNode,
  Workspace,
} from "./lib/types";

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

// Path-based routing for every mode:
//   /topics/<ancestor-slugs…>/<slug>   → topics, item resolved by the last slug
//   /chats/<slug>                      → chats
//   /ws/<slug>/<file/path>             → workspaces
// Slugs are globally unique, so the trailing topic slug alone identifies the
// item; the ancestor slugs make the URL readable + bookmarkable.
interface AppRoute {
  mode: SidebarMode;
  topicSlug: string | null;
  chatSlug: string | null;
  // The raw agent path segment — a public UUID for new links, or a legacy
  // integer id. Resolved to an internal numeric id once the agent list loads.
  agentRef: string | null;
}

function parseAppRoute(): AppRoute {
  const segs = window.location.pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  if (segs[0] === "ws")
    return { mode: "workspaces", topicSlug: null, chatSlug: null, agentRef: null };
  if (segs[0] === "agents") {
    return {
      mode: "agents",
      topicSlug: null,
      chatSlug: null,
      agentRef: segs[1] ? decodeURIComponent(segs[1]) : null,
    };
  }
  if (segs[0] === "chats") {
    return {
      mode: "chats",
      topicSlug: null,
      chatSlug: segs[1] ? decodeURIComponent(segs[1]) : null,
      agentRef: null,
    };
  }
  if (segs[0] === "topics") {
    const last = segs.length > 1 ? decodeURIComponent(segs[segs.length - 1]) : null;
    return { mode: "topics", topicSlug: last, chatSlug: null, agentRef: null };
  }
  return { mode: "topics", topicSlug: null, chatSlug: null, agentRef: null };
}

/** Ancestor → self slug chain for a topic, using the loaded tree. */
function topicSlugPath(tree: TopicNode[], topicId: number): string[] {
  const byId = new Map<number, TopicNode>();
  const walk = (nodes: TopicNode[]): void => {
    for (const n of nodes) {
      byId.set(n.id, n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(tree);
  const path: string[] = [];
  let cur: TopicNode | undefined = byId.get(topicId);
  while (cur) {
    path.unshift(cur.slug);
    cur = cur.parent_id != null ? byId.get(cur.parent_id) : undefined;
  }
  return path;
}

function topicUrl(tree: TopicNode[], topic: Topic): string {
  const segs = topicSlugPath(tree, topic.id);
  const chain = segs.length ? segs : [topic.slug];
  return "/topics/" + chain.map(encodeURIComponent).join("/");
}

function chatUrl(chat: Chat): string {
  return "/chats/" + encodeURIComponent(chat.slug);
}

// Agents are addressed by their public UUID (copilot_session_id) in the URL.
// Until the agent list has loaded we may not know the UUID yet, so fall back to
// the internal id; the URL-sync effect rewrites it to the UUID once known.
function agentUrl(agentId: number | null, agents: AgentSession[] | null): string {
  if (agentId == null) return "/agents";
  const a = agents?.find((x) => x.id === agentId);
  const ref = a?.copilot_session_id ?? String(agentId);
  return `/agents/${encodeURIComponent(ref)}`;
}

// Resolve a URL agent segment to an internal id. A pure-integer ref is a legacy
// id; anything else is a public UUID looked up in the loaded session list.
// Returns null when a UUID can't be matched yet (agents not loaded).
function resolveAgentRef(ref: string | null, agents: AgentSession[] | null): number | null {
  if (!ref) return null;
  if (/^\d+$/.test(ref)) return Number(ref);
  return agents?.find((a) => a.copilot_session_id === ref)?.id ?? null;
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
  // The active sidebar mode. The URL path owns the mode + selection, so a deep
  // link (or reload onto /topics, /chats, /ws) starts the app in that mode.
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>(() => parseAppRoute().mode);
  const [activeChat, setActiveChat] = useState<Chat | null>(null);
  const [chatListReloadKey, setChatListReloadKey] = useState(0);
  const [activeChatReloadKey, setActiveChatReloadKey] = useState(0);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [agentSettingsOpen, setAgentSettingsOpen] = useState(false);
  const [globalSettingsOpen, setGlobalSettingsOpen] = useState(false);
  const [roleSelectorOpen, setRoleSelectorOpen] = useState(false);
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [wsRoute, setWsRoute] = useState<WsRoute>(parseWsRoute);
  // Workspaces are loaded lazily when the user first enters workspaces mode.
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<number | null>(null);
  // Agents are loaded lazily when the user first enters agents mode.
  const [agents, setAgents] = useState<AgentSession[] | null>(null);
  const [activeAgentId, setActiveAgentId] = useState<number | null>(
    // A legacy integer ref resolves immediately; a UUID waits for the list.
    () => resolveAgentRef(parseAppRoute().agentRef, null),
  );
  // A URL UUID we couldn't resolve yet (agent list not loaded). Resolved once
  // the sessions arrive. Initialised from the entry URL.
  const pendingAgentRef = useRef<string | null>(
    (() => {
      const ref = parseAppRoute().agentRef;
      return ref && !/^\d+$/.test(ref) ? ref : null;
    })(),
  );
  // Topic to preselect in the new-agent form, set when "/agent" (no prompt) is
  // run from a topic. Cleared once consumed.
  const [agentDraftTopicId, setAgentDraftTopicId] = useState<number | null>(null);
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
  // Fired reminders awaiting acknowledgment, surfaced in the sidebar.
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  // Ids already seen as fired, so we only notify on newly-fired ones.
  const seenFiredRef = useRef<Set<number>>(new Set());
  // Conversations with a fired reminder, so their list rows can flag it.
  const reminderTopicIds = useMemo(
    () =>
      new Set(
        reminders.filter((r) => r.container === "topic" && r.topic_id != null).map((r) => r.topic_id!),
      ),
    [reminders],
  );
  const reminderChatIds = useMemo(
    () =>
      new Set(
        reminders.filter((r) => r.container === "chat" && r.chat_id != null).map((r) => r.chat_id!),
      ),
    [reminders],
  );

  useStreamVersion();
  const streamingTopicIds = streamStore.streamingIds("topic");
  const streamingChatIds = streamStore.streamingIds("chat");

  const settings = useSettings();
  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;
  const agentsEnabled = settings?.agents_enabled ?? false;
  const agentsAvailable = settings?.agents_available ?? false;
  const agentsUnavailableReason = settings?.agents_unavailable_reason ?? null;

  const issueContext = useIssueContext(activeTopic, setActiveTopic);

  const confirmAction = useConfirm();
  // The currently-selected agent session, surfaced in the shared header.
  const activeAgent = useMemo(
    () => (agents ?? []).find((a) => a.id === activeAgentId) ?? null,
    [agents, activeAgentId],
  );

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

  // Mirror activeAgentId into a ref so changeMode can build the agents URL.
  const activeAgentIdRef = useRef<number | null>(activeAgentId);
  useEffect(() => {
    activeAgentIdRef.current = activeAgentId;
  }, [activeAgentId]);

  // Mirror the loaded agent list into a ref so the mount-only URL sync handler
  // can resolve a UUID segment without re-subscribing.
  const agentsRef = useRef<AgentSession[] | null>(agents);
  useEffect(() => {
    agentsRef.current = agents;
  }, [agents]);

  // Once sessions load, resolve any UUID deep link that arrived before the list
  // was available (e.g. opening /agents/<uuid> cold).
  useEffect(() => {
    if (!pendingAgentRef.current || agents == null) return;
    const id = resolveAgentRef(pendingAgentRef.current, agents);
    if (id != null) {
      pendingAgentRef.current = null;
      setActiveAgentId(id);
    }
  }, [agents]);

  async function refreshTree(): Promise<void> {
    setTree(await api.topicTree());
  }

  // Reload fired reminders and fire a browser notification for any that became
  // fired since the last load (when enabled + window unfocused).
  async function loadReminders(): Promise<void> {
    let items: ReminderItem[];
    try {
      items = await api.listReminders();
    } catch {
      return; // transient — keep the previous list
    }
    if (notificationsEnabledRef.current) {
      for (const item of items) {
        if (!seenFiredRef.current.has(item.id)) {
          notifyIfUnfocused({
            title: item.title,
            body: item.note?.trim() ? `⏰ ${item.note.trim()}` : "⏰ Reminder",
            tag: `precursor-reminder-${item.id}`,
          });
        }
      }
    }
    seenFiredRef.current = new Set(items.map((i) => i.id));
    setReminders(items);
  }

  useEffect(() => {
    void loadReminders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void refreshTree();
    void skillsStore.load();
    void rolesStore.load();
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

  // ---- Path-based routing ----------------------------------------------
  // The URL path is the single source of truth for mode + selection:
  //   /topics/<ancestor-slugs…>/<slug>   /chats/<slug>   /ws/<slug>/<path>
  // `syncFromUrl` runs on mount + back/forward; the effects below push the URL
  // when the active item changes. Equality checks break the feedback loop.
  useEffect(() => {
    const syncFromUrl = (): void => {
      const r = parseAppRoute();
      setSidebarMode(r.mode);
      if (r.mode === "workspaces") {
        setWsRoute(parseWsRoute());
        return;
      }
      // Left workspaces — clear its route state so a stale slug/path can't leak.
      setWsRoute({ open: false, slug: null, path: null });
      if (r.mode === "agents") {
        if (r.agentRef == null) {
          pendingAgentRef.current = null;
          setActiveAgentId(null);
          return;
        }
        const id = resolveAgentRef(r.agentRef, agentsRef.current);
        if (id != null) {
          pendingAgentRef.current = null;
          setActiveAgentId(id);
        } else {
          // UUID not resolvable yet — stash it for the agents-load effect.
          pendingAgentRef.current = r.agentRef;
        }
        return;
      }
      if (r.mode === "topics") {
        const slug = r.topicSlug;
        if (!slug || activeTopicRef.current?.slug === slug) return;
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
        return;
      }
      // chats
      const slug = r.chatSlug;
      if (!slug || activeChatRef.current?.slug === slug) return;
      void (async () => {
        try {
          const c = await api.getChatBySlug(slug);
          setActiveChat(c);
          try {
            await api.markChatRead(c.id);
            setChatListReloadKey((k) => k + 1);
          } catch {
            // non-fatal
          }
        } catch {
          // unknown slug — ignore
        }
      })();
    };
    syncFromUrl();
    window.addEventListener("popstate", syncFromUrl);
    return () => window.removeEventListener("popstate", syncFromUrl);
  }, []);

  // activeTopic -> /topics/<…>/<slug>. pushState for a different item (so
  // back/forward walks topics); replaceState to *refine* the same item's path
  // (e.g. once the tree loads and the ancestor chain is known). Never strips
  // ancestors, so a deep link like /topics/a/b/c survives the initial load.
  useEffect(() => {
    if (sidebarMode !== "topics" || !activeTopic) return;
    const target = topicUrl(tree, activeTopic);
    if (window.location.pathname === target) return;
    const curSegs = window.location.pathname
      .replace(/\/+$/, "")
      .split("/")
      .filter(Boolean);
    const lastSeg = decodeURIComponent(curSegs[curSegs.length - 1] ?? "");
    if (lastSeg === activeTopic.slug) {
      // Same item already in the URL — only extend the ancestor chain.
      const targetSegs = target.split("/").filter(Boolean);
      if (targetSegs.length > curSegs.length) history.replaceState(null, "", target);
    } else {
      history.pushState(null, "", target);
    }
  }, [activeTopic, sidebarMode, tree]);

  // activeChat -> /chats/<slug>.
  useEffect(() => {
    if (sidebarMode !== "chats" || !activeChat) return;
    const target = chatUrl(activeChat);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeChat, sidebarMode]);

  // activeAgentId -> /agents/<uuid> (or /agents when nothing is selected). The
  // canonical URL uses the public UUID; depends on `agents` so the link is
  // rewritten from a transient integer fallback once the list resolves.
  useEffect(() => {
    if (sidebarMode !== "agents") return;
    // Don't clobber a deep-link URL whose agent we haven't resolved yet (the
    // list may still be loading). Overwriting it with "/agents" here would also
    // drop the UUID before the agents-load effect can resolve it.
    if (activeAgentId == null && pendingAgentRef.current) return;
    const target = agentUrl(activeAgentId, agents);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeAgentId, sidebarMode, agents]);

  // Deep-link from an "agent exchange" message badge (posted into a topic/chat)
  // back to its Agents-mode session.
  useEffect(() => {
    function onOpenAgent(e: Event): void {
      const detail = (e as CustomEvent<{ id: number | null; topicId?: number }>).detail;
      const id = detail?.id ?? null;
      // A null id opens the new-agent form; carry the topic so it's preselected.
      setAgentDraftTopicId(id == null ? (detail?.topicId ?? null) : null);
      setActiveAgentId(id);
      setWsRoute({ open: false, slug: null, path: null });
      setSidebarMode("agents");
    }
    window.addEventListener("precursor:open-agent", onOpenAgent);
    return () => window.removeEventListener("precursor:open-agent", onOpenAgent);
  }, []);

  // Switch sidebar mode, pushing the URL for that mode (the active item's path
  // when there is one, else the mode's base path).
  function changeMode(next: SidebarMode): void {
    if (next === sidebarMode) return;
    let target = "/topics";
    if (next === "topics") {
      target = activeTopicRef.current
        ? topicUrl(treeRef.current, activeTopicRef.current)
        : "/topics";
    } else if (next === "chats") {
      target = activeChatRef.current ? chatUrl(activeChatRef.current) : "/chats";
    } else if (next === "agents") {
      target = agentUrl(activeAgentIdRef.current, agentsRef.current);
    } else {
      target = "/ws";
    }
    history.pushState(null, "", target);
    setWsRoute(next === "workspaces" ? parseWsRoute() : { open: false, slug: null, path: null });
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
    streamStore.setOnComplete((key) => {
      const [kind, rawId] = key.split(":");
      const id = Number(rawId);
      void (async () => {
        if (kind === "chat") {
          if (activeChatRef.current?.id === id) {
            try {
              await api.markChatRead(id);
            } catch {
              // non-fatal
            }
          }
          setChatListReloadKey((k) => k + 1);
          return;
        }
        if (activeTopicRef.current?.id === id) {
          try {
            await api.markTopicRead(id);
          } catch {
            // non-fatal
          }
        }
        await refreshTree();
        // Foreground turn finished — notify if the user has switched away.
        maybeNotify(id);
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
          // A chat turn changed — refresh the list badges and, if the user is
          // viewing it, remount the panel so it re-fetches from scratch.
          setChatListReloadKey((k) => k + 1);
          if (activeChatRef.current?.id === event.chat_id) {
            setActiveChatReloadKey((k) => k + 1);
          }
          return;
        }
        // Sidebar badge tracking depends on the tree, so always refresh.
        void refreshTree();
        if (activeTopicRef.current?.id === event.topic_id) {
          // Re-mount ChatPanel so it re-fetches messages from scratch.
          setChatReloadKey((k) => k + 1);
        }
      } else if (event.type === "stream.started") {
        if (event.chat_id != null) {
          streamStore.setRemoteStreaming(convKey("chat", event.chat_id), true);
        } else if (event.topic_id != null) {
          streamStore.setRemoteStreaming(convKey("topic", event.topic_id), true);
        }
      } else if (event.type === "stream.ended") {
        if (event.chat_id != null) {
          streamStore.setRemoteStreaming(convKey("chat", event.chat_id), false);
          setChatListReloadKey((k) => k + 1);
          return;
        }
        if (event.topic_id != null) {
          streamStore.setRemoteStreaming(convKey("topic", event.topic_id), false);
          // A turn finished elsewhere (another window or a scheduled task). The
          // driving window's own echo is filtered by client id, so this only
          // covers background completions — notify if enabled + unfocused.
          maybeNotify(event.topic_id);
        }
      } else if (event.type === "reminder.changed") {
        // A reminder was set, fired, or cleared (possibly by the background
        // ticker). Reload the sidebar section; loadReminders also notifies for
        // any newly-fired ones.
        void loadReminders();
      } else if (event.type === "agent.changed") {
        // An agent session was created, advanced, or finished (possibly in the
        // background). Refresh the list so statuses/badges stay current; the
        // AgentView refreshes its own timeline.
        void loadAgents();
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

  // Inline rename from the sidebar tree (double-click a topic's name).
  async function handleRenameTopic(id: number, title: string): Promise<void> {
    const updated = await api.updateTopic(id, { title });
    if (activeTopicRef.current?.id === id) setActiveTopic(updated);
    await refreshTree();
  }

  async function handleRenameAgent(id: number, title: string): Promise<void> {
    await api.renameAgent(id, title);
    await loadAgents();
  }

  async function handleStopAgent(id: number): Promise<void> {
    await api.cancelAgent(id);
    await loadAgents();
  }

  async function handleDeleteAgent(agent: AgentSession): Promise<void> {
    if (
      !(await confirmAction({
        message: `Delete agent “${agent.title}”? Its session state is discarded.`,
        confirmLabel: "Delete",
        variant: "danger",
      }))
    )
      return;
    await api.deleteAgent(agent.id);
    if (activeAgentId === agent.id) setActiveAgentId(null);
    await loadAgents();
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

  // Open the conversation behind a fired reminder, switching mode if needed.
  async function handleReminderSelect(item: ReminderItem): Promise<void> {
    try {
      if (item.container === "topic" && item.topic_id != null) {
        changeMode("topics");
        await handleSelect(item.topic_id);
      } else if (item.container === "chat" && item.chat_id != null) {
        changeMode("chats");
        await handleSelectChat(await api.getChat(item.chat_id));
      }
    } catch {
      // conversation may have been deleted — refresh the list to drop it
      void loadReminders();
    }
  }

  // Acknowledge a fired reminder ("Done"): clear it and refresh the section.
  async function handleReminderDone(item: ReminderItem): Promise<void> {
    const id = item.container === "topic" ? item.topic_id : item.chat_id;
    if (id == null) return;
    try {
      await api.clearReminder(item.container, id);
    } catch {
      // already gone — fall through to reload
    }
    await loadReminders();
    // Remount the active panel so its banner clears if it was the one acked.
    if (item.container === "topic" && activeTopicRef.current?.id === item.topic_id) {
      setChatReloadKey((k) => k + 1);
    } else if (item.container === "chat" && activeChatRef.current?.id === item.chat_id) {
      setActiveChatReloadKey((k) => k + 1);
    }
  }

  // Re-fetch the active chat + nudge the list (after rename / pin / clear).
  async function refreshActiveChat(): Promise<void> {
    setChatListReloadKey((k) => k + 1);
    const active = activeChatRef.current;
    if (!active) return;
    try {
      setActiveChat(await api.getChat(active.id));
    } catch {
      // chat may have been deleted elsewhere; ignore
    }
  }

  async function toggleChatPin(): Promise<void> {
    if (!activeChat) return;
    const updated = await api.updateChat(activeChat.id, { pinned: !activeChat.pinned });
    setActiveChat(updated);
    setChatListReloadKey((k) => k + 1);
  }

  async function handleRenameChat(id: number, title: string): Promise<void> {
    const updated = await api.updateChat(id, { title });
    if (activeChatRef.current?.id === id) setActiveChat(updated);
    setChatListReloadKey((k) => k + 1);
  }

  async function handleCreateChat(): Promise<void> {
    const chat = await api.createChat({ title: "New chat" });
    setActiveChat(chat);
    setChatListReloadKey((k) => k + 1);
  }

  // The sidebar header's single "New" button adapts to the active mode so the
  // create affordance lives in the same place across Topics / Chats / Files.
  function handleNew(): void {
    if (sidebarMode === "topics") handleCreate(null);
    else if (sidebarMode === "chats") void handleCreateChat();
    else if (sidebarMode === "agents") setActiveAgentId(null);
    else setCreateWorkspaceOpen(true);
  }

  // ---- Workspaces -------------------------------------------------------
  const activeWorkspace =
    workspaces?.find((w) => w.id === activeWorkspaceId) ?? null;
  // The route path only applies to the workspace named in the URL.
  const workspaceInitialPath =
    activeWorkspace && activeWorkspace.slug === wsRoute.slug ? wsRoute.path : null;

  // ---- Assistant roles --------------------------------------------------
  // The header role selector and the `/role` command both funnel through here.
  // Selecting the default role persists null (which resolves to default
  // server-side). Updates are persisted per-discussion and reflected locally.
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (sidebarMode === "topics" && activeTopic) {
      const updated = await api.updateTopic(activeTopic.id, { role_id: roleId });
      if (activeTopicRef.current?.id === activeTopic.id) setActiveTopic(updated);
    } else if (sidebarMode === "chats" && activeChat) {
      const updated = await api.updateChat(activeChat.id, { role_id: roleId });
      if (activeChatRef.current?.id === activeChat.id) setActiveChat(updated);
    } else if (sidebarMode === "workspaces" && activeWorkspace) {
      const updated = await api.updateWorkspace(activeWorkspace.id, {
        role_id: roleId,
      });
      setWorkspaces((prev) =>
        prev ? prev.map((w) => (w.id === updated.id ? updated : w)) : prev,
      );
    }
  }

  const activeRoleId =
    sidebarMode === "topics"
      ? (activeTopic?.role_id ?? null)
      : sidebarMode === "chats"
        ? (activeChat?.role_id ?? null)
        : (activeWorkspace?.role_id ?? null);

  const hasActiveDiscussion =
    (sidebarMode === "topics" && !!activeTopic) ||
    (sidebarMode === "chats" && !!activeChat) ||
    (sidebarMode === "workspaces" && !!activeWorkspace);

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

  // ---- Agents -----------------------------------------------------------
  async function loadAgents(): Promise<AgentSession[]> {
    try {
      const list = await api.listAgents();
      setAgents(list);
      return list;
    } catch {
      setAgents([]);
      return [];
    }
  }

  // Lazily load agent sessions the first time the user enters agents mode
  // (only when the feature is enabled).
  useEffect(() => {
    if (sidebarMode !== "agents" || agents !== null || !agentsEnabled) return;
    void loadAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode, agentsEnabled]);

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
      <DetachedDraftHost />
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
            streamingIds={streamingChatIds}
            reminderChatIds={reminderChatIds}
            onSelect={handleSelectChat}
            onOpenSettings={(chat) => {
              setActiveChat(chat);
              setChatSettingsOpen(true);
            }}
            onChatsChanged={() => void refreshActiveChat()}
          />
        }
        workspaceSlot={
          <WorkspaceList
            workspaces={workspaces}
            activeId={activeWorkspaceId}
            onSelect={handleSelectWorkspace}
          />
        }
        agentSlot={
          <AgentList
            agents={agents ?? []}
            activeId={activeAgentId}
            enabled={agentsEnabled}
            onSelect={(id) => setActiveAgentId(id)}
            onRename={handleRenameAgent}
          />
        }
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        onSelect={handleSelect}
        onNew={handleNew}
        onCreate={handleCreate}
        onRename={handleRenameTopic}
        onCreateSchedule={() => setScheduleModal(null)}
        onEditSchedule={handleEditSchedule}
        reminders={reminders}
        reminderTopicIds={reminderTopicIds}
        onReminderSelect={handleReminderSelect}
        onReminderDone={handleReminderDone}
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
                {activeTopic ? (
                  <InlineTitle
                    title={activeTopic.title}
                    onRename={(t) => handleRenameTopic(activeTopic.id, t)}
                    className="truncate font-medium"
                    inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
                  />
                ) : (
                  <span className="truncate font-medium">Select or create a topic</span>
                )}
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
          ) : sidebarMode === "workspaces" ? (
            <span className="truncate font-medium min-w-0 flex-1">
              {activeWorkspace ? activeWorkspace.name : "Workspaces"}
            </span>
          ) : (
            <>
              {activeAgent ? (
                <>
                  <InlineTitle
                    title={activeAgent.title}
                    onRename={(t) => handleRenameAgent(activeAgent.id, t)}
                    className="truncate font-medium min-w-0 flex-1"
                    inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
                  />
                  <AgentStatusBadge status={activeAgent.status} />
                  {activeAgent.topic_id != null && (
                    <button
                      type="button"
                      onClick={() => {
                        const tid = activeAgent.topic_id;
                        if (tid == null) return;
                        changeMode("topics");
                        void handleSelect(tid);
                      }}
                      className="group inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[11px] font-medium text-violet-600 hover:bg-violet-500/20 dark:text-violet-300"
                      title="Open the associated topic"
                      data-tooltip="Open the associated topic"
                    >
                      <MessagesSquare size={12} />
                      <span className="max-w-[12rem] truncate">
                        {findTitle(tree, activeAgent.topic_id) ?? "Topic"}
                      </span>
                      <ArrowUpRight
                        size={12}
                        className="opacity-60 transition group-hover:opacity-100"
                      />
                    </button>
                  )}
                  <button
                    className="p-2 rounded hover:bg-surface shrink-0"
                    aria-label="Agent settings"
                    data-tooltip="Agent settings"
                    onClick={() => setAgentSettingsOpen(true)}
                  >
                    <SettingsIcon size={18} />
                  </button>
                  {(activeAgent.status === "running" ||
                    activeAgent.status === "pending" ||
                    activeAgent.status === "needs_approval") && (
                    <button
                      className="p-2 rounded hover:bg-surface shrink-0 text-muted hover:text-red-500"
                      aria-label="Stop agent"
                      data-tooltip="Stop agent"
                      onClick={() => void handleStopAgent(activeAgent.id)}
                    >
                      <Square size={18} />
                    </button>
                  )}
                  <button
                    className="p-2 rounded hover:bg-surface shrink-0 text-muted hover:text-red-500"
                    aria-label="Delete agent"
                    data-tooltip="Delete agent"
                    onClick={() => void handleDeleteAgent(activeAgent)}
                  >
                    <Trash2 size={18} />
                  </button>
                </>
              ) : (
                <span className="truncate font-medium min-w-0 flex-1">Agents</span>
              )}
            </>
          )}
          {hasActiveDiscussion && (
            <RoleSelector
              value={activeRoleId}
              onChange={(roleId) => void setRoleForActive(roleId)}
              open={roleSelectorOpen}
              onOpenChange={setRoleSelectorOpen}
            />
          )}
        </header>

        <McpAuthBanner />

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
                onRemindersChanged={loadReminders}
                onSetRole={setRoleForActive}
                onOpenRoleSelector={() => setRoleSelectorOpen(true)}
              />
            ) : (
              <EmptyHero label="No topic selected." />
            )
          ) : sidebarMode === "chats" ? (
            activeChat ? (
              <ChatSessionPanel
                key={`${activeChat.id}:${activeChatReloadKey}`}
                chat={activeChat}
                onChatUpdated={refreshActiveChat}
                onArchived={() => {
                  setActiveChat(null);
                  setChatListReloadKey((k) => k + 1);
                }}
                onRemindersChanged={loadReminders}
                onSetRole={setRoleForActive}
                onOpenRoleSelector={() => setRoleSelectorOpen(true)}
              />
            ) : (
              <EmptyHero label="No chat selected." />
            )
          ) : sidebarMode === "workspaces" ? (
            workspaces === null ? (
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
                onSetRole={setRoleForActive}
                onOpenRoleSelector={() => setRoleSelectorOpen(true)}
              />
            ) : (
              <EmptyHero label="No workspaces yet." />
            )
          ) : (
            <AgentView
              agents={agents ?? []}
              agentId={activeAgentId}
              enabled={agentsEnabled}
              available={agentsAvailable}
              unavailableReason={agentsUnavailableReason}
              onReload={() => void loadAgents()}
              onSelect={(id) => setActiveAgentId(id)}
              onOpenSettings={() => setGlobalSettingsOpen(true)}
              draftTopicId={agentDraftTopicId}
            />
          )}
        </div>
      </main>

      {globalSettingsOpen && (
        <SettingsPanel onClose={() => setGlobalSettingsOpen(false)} />
      )}

      {chatSettingsOpen && activeChat && (
        <ChatSettingsPanel
          chat={activeChat}
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
            changeMode("topics");
            setActiveTopic(topic);
            await refreshTree();
          }}
        />
      )}

      {agentSettingsOpen && activeAgent && (
        <AgentSettingsPanel
          agent={activeAgent}
          onClose={() => setAgentSettingsOpen(false)}
          onSaved={(updated) => {
            setAgents((prev) =>
              (prev ?? []).map((a) => (a.id === updated.id ? updated : a)),
            );
            setAgentSettingsOpen(false);
          }}
          onArchived={() => {
            setAgentSettingsOpen(false);
            if (activeAgentId === activeAgent.id) setActiveAgentId(null);
            void loadAgents();
          }}
          onDeleted={() => {
            setAgentSettingsOpen(false);
            if (activeAgentId === activeAgent.id) setActiveAgentId(null);
            void loadAgents();
          }}
        />
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
        <ArchivePanel
          onClose={() => setArchiveOpen(false)}
          onTopicRestored={async () => {
            await refreshTree();
          }}
          onTopicDeleted={async (id) => {
            if (activeTopic?.id === id) setActiveTopic(null);
            await refreshTree();
          }}
          onChatRestored={() => setChatListReloadKey((k) => k + 1)}
          onChatDeleted={(id) => {
            if (activeChat?.id === id) setActiveChat(null);
            setChatListReloadKey((k) => k + 1);
          }}
          onAgentRestored={() => void loadAgents()}
          onAgentDeleted={(id) => {
            if (activeAgentId === id) setActiveAgentId(null);
            void loadAgents();
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

