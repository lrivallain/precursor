import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  ChevronRight,
  ExternalLink,
  MessagesSquare,
  Pin,
  PinOff,
  Search,
  Settings as SettingsIcon,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { Sidebar, SectionRail, type SidebarMode } from "./components/Sidebar";
import { GithubIcon as Github } from "./components/icons/GithubIcon";
import { CommandPalette } from "./components/CommandPalette";
import { ChatPanel } from "./components/ChatPanel";
import { ChatList } from "./components/ChatList";
import { ChatSessionPanel } from "./components/ChatSessionPanel";
import { ChatSettingsPanel } from "./components/ChatSettingsPanel";
import { McpAuthBanner } from "./components/McpAuthBanner";
import { SettingsPanel } from "./components/SettingsPanel";
import { TopicSettingsPanel } from "./components/TopicSettingsPanel";
import { ChatStartHero, TopicStartHero } from "./components/StartHero";
import { HomePage } from "./components/HomePage";
import { ArchivePanel } from "./components/ArchivePanel";
import { IssueStatusBadge } from "./components/IssueStatusBadge";
import { IssueLabelChip, IssueStateBadge } from "./components/IssueTags";
import {
  CreateWorkspaceModal,
  WorkspaceView,
} from "./components/WorkspaceView";
import { WorkspaceList } from "./components/WorkspaceList";
import { LiveList } from "./components/LiveList";
import { LiveView } from "./components/LiveView";
import { LiveStartHero } from "./components/LiveStartHero";
import { AgentList } from "./components/AgentList";
import { AgentSettingsPanel } from "./components/AgentSettingsPanel";
import { AgentStatusBadge } from "./components/AgentStatusBadge";
import { AgentView } from "./components/AgentView";
import { KanbanBoard } from "./components/KanbanBoard";
import { ProjectList } from "./components/ProjectList";
import { DetachedDraftHost } from "./components/DetachedDraftHost";
import { InlineTitle } from "./components/InlineTitle";
import { useConfirm } from "./components/ConfirmDialog";
import { RoleSelector } from "./components/RoleSelector";
import { TooltipProvider } from "./components/Tooltip";
import { api, apiErrorMessage } from "./lib/api";
import { SearchHighlightProvider } from "./lib/searchHighlight";
import { eventBus } from "./lib/events";
import { notifyIfUnfocused } from "./lib/notifications";
import { skillsStore } from "./lib/skillsStore";
import { rolesStore } from "./lib/rolesStore";
import { useSettings } from "./lib/settingsStore";
import { streamStore, useStreamVersion, convKey } from "./lib/streamStore";
import { useIssueContext } from "./lib/useIssueContext";
import { useSidebarNavStyle } from "./lib/useSidebarNavStyle";
import type {
  AgentSession,
  Chat,
  MeetingSession,
  ProjectSummary,
  ReminderItem,
  SearchResult,
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
  liveSlug: string | null;
  // The raw agent path segment — a public UUID for new links, or a legacy
  // integer id. Resolved to an internal numeric id once the agent list loads.
  agentRef: string | null;
  // The selected ProjectV2 URL segment (a per-owner number, optionally with a
  // title slug) when on the kanban route. Resolved to the opaque node id once
  // the project list loads.
  kanbanProjectRef: string | null;
  // The issue/PR number from the URL hash (e.g. "#94") on the kanban route,
  // selecting which card's detail preview to open. null when the hash is absent
  // or not a positive integer.
  kanbanItemRef: number | null;
}

// Parse a "#<number>" URL fragment into an issue/PR number. Anything that isn't
// a positive integer is ignored so a stray hash can't select a phantom card.
function parseHashNumber(hash: string): number | null {
  const raw = hash.replace(/^#/, "").trim();
  if (!/^\d+$/.test(raw)) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

function parseAppRoute(): AppRoute {
  const segs = window.location.pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const base: AppRoute = {
    mode: "topics",
    topicSlug: null,
    chatSlug: null,
    liveSlug: null,
    agentRef: null,
    kanbanProjectRef: null,
    kanbanItemRef: null,
  };
  if (segs[0] === "ws") return { ...base, mode: "workspaces" };
  if (segs[0] === "agents") {
    return { ...base, mode: "agents", agentRef: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "chats") {
    return { ...base, mode: "chats", chatSlug: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "live") {
    return { ...base, mode: "live", liveSlug: segs[1] ? decodeURIComponent(segs[1]) : null };
  }
  if (segs[0] === "kanban") {
    return {
      ...base,
      mode: "kanban",
      kanbanProjectRef: segs[1] ? decodeURIComponent(segs[1]) : null,
      kanbanItemRef: parseHashNumber(window.location.hash),
    };
  }
  if (segs[0] === "topics") {
    const last = segs.length > 1 ? decodeURIComponent(segs[segs.length - 1]) : null;
    return { ...base, mode: "topics", topicSlug: last };
  }
  return base;
}

/** The home launcher lives at the root path `/` (no path segments). */
function isHomePath(): boolean {
  return window.location.pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean).length === 0;
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

/** Ancestor chain (root → immediate parent, excluding self) for a topic. */
function topicAncestors(tree: TopicNode[], topicId: number): TopicNode[] {
  const byId = new Map<number, TopicNode>();
  const walk = (nodes: TopicNode[]): void => {
    for (const n of nodes) {
      byId.set(n.id, n);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(tree);
  const chain: TopicNode[] = [];
  let cur = byId.get(topicId);
  let parentId = cur?.parent_id ?? null;
  while (parentId != null) {
    cur = byId.get(parentId);
    if (!cur) break;
    chain.unshift(cur);
    parentId = cur.parent_id ?? null;
  }
  return chain;
}

function chatUrl(chat: Chat): string {
  return "/chats/" + encodeURIComponent(chat.slug);
}

function liveUrl(session: MeetingSession | null): string {
  if (session == null) return "/live";
  return "/live/" + encodeURIComponent(session.slug);
}

function kanbanUrl(project: ProjectSummary | null): string {
  if (!project) return "/kanban";
  return "/kanban/" + encodeURIComponent(projectSlug(project));
}

// Human-readable, stable slug for a ProjectV2: its per-owner `number` (the
// resolvable key) plus a slugified title for readability, e.g. "4-work-ms".
function projectSlug(project: ProjectSummary): string {
  const base = project.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return base ? `${project.number}-${base}` : String(project.number);
}

// Resolve a kanban URL segment back to a project number. Only the leading
// integer is significant, so the trailing title slug is cosmetic and can drift
// without breaking the link (e.g. "4", "4-work-ms", "4-renamed" all resolve).
function projectRefNumber(ref: string | null): number | null {
  if (!ref) return null;
  const match = /^(\d+)/.exec(ref);
  return match ? Number(match[1]) : null;
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

// Agent statuses that represent a finished/paused turn (not actively running).
// Used to re-mark the actively-viewed agent read once per turn rather than on
// every streamed event.
const AGENT_SETTLED_STATUSES = new Set([
  "idle",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "needs_approval",
]);

// Auto-marking a conversation read on an *incoming* reply should only happen in
// the tab the user is actually looking at. A tab merely left open on a
// conversation in the background must not clear the unread for everyone (read
// state is shared server-side) — otherwise a reply that arrives while you're in
// another tab/app never shows as unread. Explicit actions (clicking a
// conversation open) mark read regardless; this gate is only for event-driven
// auto-marks. Mirrors the standard "unread accrues while the window isn't
// focused" behaviour (and how maybeNotify already keys off focus).
function windowFocused(): boolean {
  return typeof document !== "undefined" && document.hasFocus();
}

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

function findNode(nodes: TopicNode[], topicId: number): TopicNode | null {
  for (const node of nodes) {
    if (node.id === topicId) return node;
    if (node.children?.length) {
      const hit = findNode(node.children, topicId);
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
  // The root path `/` shows the home launcher instead of any mode's content.
  const [atHome, setAtHome] = useState<boolean>(() => isHomePath());
  // Active "find" term for the open conversation, seeded from the ?q= URL param
  // and set when a content-search hit is opened. Highlights matches in message
  // bodies; empty means no highlighting.
  const [searchHighlight, setSearchHighlight] = useState<string>(
    () => new URLSearchParams(window.location.search).get("q") ?? "",
  );
  // The conversation the current highlight belongs to (a `${mode}:${id}` key),
  // so we can auto-clear the highlight when the user navigates to a *different*
  // conversation. `pendingHighlightKeyRef` holds the target of an in-flight
  // search-open so the transition to it isn't mistaken for a navigation-away.
  const highlightKeyRef = useRef<string | null>(null);
  const pendingHighlightKeyRef = useRef<string | null>(null);
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
  // Live meeting sessions are loaded lazily when the user first enters live mode.
  const [meetingSessions, setMeetingSessions] = useState<MeetingSession[] | null>(null);
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  // GitHub Projects v2 are loaded lazily when the user first enters kanban mode.
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  // The opaque ProjectV2 node id of the active board (board fetches need it).
  // The URL carries a number-based ref instead, resolved to this once the
  // project list loads.
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  // A kanban URL ref (project number/slug) awaiting resolution against the
  // loaded project list. Initialised from the entry URL, cleared once resolved.
  const pendingProjectRef = useRef<string | null>(parseAppRoute().kanbanProjectRef);
  // The issue/PR number selected via the URL hash (e.g. /kanban/4-work-ms#94).
  // Drives the board's detail preview and is kept in sync with the hash both
  // ways: the URL opens the preview, and opening/closing a card rewrites it.
  const [kanbanItemNumber, setKanbanItemNumber] = useState<number | null>(
    parseAppRoute().kanbanItemRef,
  );
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
  // Vertical-nav choice, shared with the sidebar. Drives whether the home
  // launcher also shows the standalone rail ("tabs" has no standalone form).
  const [navStyle] = useSidebarNavStyle();
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The parent topic preselected in the inline "new topic" form (set by the
  // sidebar "+" and the tree's per-node "+ child"). `null` means top level.
  const [topicDraftParentId, setTopicDraftParentId] = useState<number | null>(null);
  // Bumped on every create action so the inline form remounts (and re-focuses
  // its title) even when the preselected parent is unchanged.
  const [topicDraftNonce, setTopicDraftNonce] = useState(0);
  const [chatReloadKey, setChatReloadKey] = useState(0);
  // Total unread across chats, lifted from ChatList so the mode switcher can
  // badge the Chats tab even when that list isn't mounted.
  const [chatsUnread, setChatsUnread] = useState(0);
  // Previous per-agent unread counts, so loadAgents can detect background
  // completions and fire a browser notification for newly-unread sessions.
  const agentUnreadRef = useRef<Map<number, number> | null>(null);
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
  const globalGithubRepo = (settings?.github_repo ?? "").trim();
  // The kanban board needs both a configured repo and the GitHub issue surface
  // turned on — otherwise there are no ProjectsV2 to render.
  const kanbanEnabled = issueAssociationsEnabled && globalGithubRepo.length > 0;
  const agentsEnabled = settings?.agents_enabled ?? false;
  const liveEnabled = settings?.live_enabled ?? true;
  const [liveRecordingId, setLiveRecordingId] = useState<number | null>(null);
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

  // Mirror the active meeting session into refs so changeMode / URL sync can
  // build the /live URL without re-subscribing.
  const meetingSessionsRef = useRef<MeetingSession[] | null>(meetingSessions);
  useEffect(() => {
    meetingSessionsRef.current = meetingSessions;
  }, [meetingSessions]);
  const activeSessionIdRef = useRef<number | null>(activeSessionId);
  useEffect(() => {
    activeSessionIdRef.current = activeSessionId;
  }, [activeSessionId]);

  const activeProjectIdRef = useRef<string | null>(activeProjectId);
  useEffect(() => {
    activeProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  // Mirror the loaded project list so changeMode / URL sync can resolve the
  // active project's number-based slug without re-subscribing.
  const projectsRef = useRef<ProjectSummary[] | null>(projects);
  useEffect(() => {
    projectsRef.current = projects;
  }, [projects]);

  // Mirror the current sidebar mode into a ref. The active item refs persist
  // across mode switches (changeMode doesn't clear them), so "the user is
  // actually looking at this conversation" means its ref matches AND its mode is
  // on screen. The (registered-once) event handlers read this to avoid marking a
  // conversation read when a background update lands in a mode the user left.
  const sidebarModeRef = useRef<SidebarMode>(sidebarMode);
  useEffect(() => {
    sidebarModeRef.current = sidebarMode;
  }, [sidebarMode]);

  // Single source of truth for "which conversation is actually on screen right
  // now". A conversation is only being viewed when its type matches the current
  // sidebar mode AND it's the active item — the active refs persist across mode
  // switches, so checking the id alone would treat a conversation the user left
  // (e.g. a still-streaming topic they navigated away from) as "viewed" and
  // wrongly mark it read when its turn finishes. Every auto-mark-read decision
  // funnels through this so read state stays reliable.
  type Viewed =
    | { kind: "topic"; id: number }
    | { kind: "chat"; id: number }
    | { kind: "agent"; id: number }
    | null;
  const currentlyViewed = useCallback((): Viewed => {
    const mode = sidebarModeRef.current;
    if (mode === "topics" && activeTopicRef.current) {
      return { kind: "topic", id: activeTopicRef.current.id };
    }
    if (mode === "chats" && activeChatRef.current) {
      return { kind: "chat", id: activeChatRef.current.id };
    }
    if (mode === "agents" && activeAgentIdRef.current != null) {
      return { kind: "agent", id: activeAgentIdRef.current };
    }
    return null;
  }, []);
  const isViewing = useCallback(
    (kind: "topic" | "chat" | "agent", id: number): boolean => {
      const v = currentlyViewed();
      return v != null && v.kind === kind && v.id === id;
    },
    [currentlyViewed],
  );

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
    setTree(await api.topics.tree());
  }

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

  // Reload fired reminders and fire a browser notification for any that became
  // fired since the last load (when enabled + window unfocused).
  async function loadReminders(): Promise<void> {
    let items: ReminderItem[];
    try {
      items = await api.reminders.list();
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
    void refreshChatsUnread();
    void skillsStore.load();
    void rolesStore.load();
  }, []);

  // Reflect the total unread count in the tab title (always, independent of the
  // notification permission/setting). Cleared title falls back to the base.
  const topicsUnread = useMemo(() => totalUnread(tree), [tree]);
  // Exclude the agent you're actively viewing (agents mode) from the tab total:
  // unlike topics/chats it isn't re-marked read on every incoming event, so its
  // backend count would otherwise keep the Agents tab badged while you watch it.
  const agentsUnread = useMemo(() => {
    const viewingId = sidebarMode === "agents" ? activeAgentId : null;
    return (agents ?? []).reduce(
      (n, a) => n + (a.id === viewingId ? 0 : (a.unread_count ?? 0)),
      0,
    );
  }, [agents, activeAgentId, sidebarMode]);
  const unreadByMode = useMemo(
    () => ({ topics: topicsUnread, chats: chatsUnread, agents: agentsUnread }),
    [topicsUnread, chatsUnread, agentsUnread],
  );
  useEffect(() => {
    const n = topicsUnread + chatsUnread + agentsUnread;
    document.title = n > 0 ? `(${n}) ${BASE_TITLE}` : BASE_TITLE;
  }, [topicsUnread, chatsUnread, agentsUnread]);

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
      // Keep the highlight term in step with the URL for reloads / back-forward.
      // Reset the ownership refs so the highlight adopts whichever conversation
      // the URL resolves to (rather than clearing on that first resolution).
      const urlTerm = new URLSearchParams(window.location.search).get("q") ?? "";
      setSearchHighlight(urlTerm);
      if (urlTerm) {
        highlightKeyRef.current = null;
        pendingHighlightKeyRef.current = null;
      }
      if (isHomePath()) {
        setAtHome(true);
        setWsRoute({ open: false, slug: null, path: null });
        return;
      }
      setAtHome(false);
      const r = parseAppRoute();
      setSidebarMode(r.mode);
      if (r.mode === "workspaces") {
        setWsRoute(parseWsRoute());
        return;
      }
      // Left workspaces — clear its route state so a stale slug/path can't leak.
      setWsRoute({ open: false, slug: null, path: null });
      if (r.mode === "kanban") {
        // Keep the hash-driven preview selection in step with the URL (initial
        // load + back/forward).
        setKanbanItemNumber(r.kanbanItemRef);
        if (r.kanbanProjectRef == null) {
          pendingProjectRef.current = null;
          setActiveProjectId(null);
          return;
        }
        const num = projectRefNumber(r.kanbanProjectRef);
        const match = projectsRef.current?.find((p) => p.number === num);
        if (match) {
          pendingProjectRef.current = null;
          setActiveProjectId(match.id);
        } else {
          // Projects not loaded yet — stash the ref for the load effect.
          pendingProjectRef.current = r.kanbanProjectRef;
        }
        return;
      }
      if (r.mode === "live") {
        const slug = r.liveSlug;
        if (!slug) {
          setActiveSessionId(null);
          return;
        }
        const existing = meetingSessionsRef.current?.find((s) => s.slug === slug);
        if (existing) {
          setActiveSessionId(existing.id);
          return;
        }
        void loadMeetingSessions().then((list) => {
          const found = list.find((s) => s.slug === slug);
          setActiveSessionId(found ? found.id : null);
        });
        return;
      }
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
            const t = await api.topics.getBySlug(slug);
            setActiveTopic(t);
            try {
              await api.topics.markRead(t.id);
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
    if (atHome) return;
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
  }, [activeTopic, sidebarMode, tree, atHome]);

  // activeChat -> /chats/<slug>.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "chats" || !activeChat) return;
    const target = chatUrl(activeChat);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeChat, sidebarMode, atHome]);

  // activeSession -> /live/<slug> (or /live when nothing is selected).
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "live") return;
    const active = meetingSessions?.find((s) => s.id === activeSessionId) ?? null;
    const target = liveUrl(active);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeSessionId, meetingSessions, sidebarMode, atHome]);

  // activeProjectId -> /kanban/<number>-<slug> (or /kanban when none selected).
  // Depends on `projects` so a board opened before the list resolves gets its
  // slug rewritten once the project's number/title are known.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "kanban") return;
    // A deep link we haven't resolved yet — leave the URL untouched so its
    // number ref survives until the load effect maps it to a project.
    if (activeProjectId == null && pendingProjectRef.current) return;
    const active = projects?.find((p) => p.id === activeProjectId) ?? null;
    const target = kanbanUrl(active);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeProjectId, projects, sidebarMode, atHome]);

  // kanbanItemNumber -> URL hash ("#<number>"), so an open card is bookmarkable
  // and back/forward toggles the preview. Only the hash is touched here; the
  // pathname effect above owns the /kanban/<project> segment. Waits for the
  // active project to resolve so a deep-linked "#94" survives until the board
  // is ready to open it.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "kanban") return;
    if (activeProjectId == null) return;
    const desired = kanbanItemNumber != null ? `#${kanbanItemNumber}` : "";
    if (window.location.hash === desired) return;
    const target = window.location.pathname + window.location.search + desired;
    history.pushState(null, "", target);
  }, [kanbanItemNumber, activeProjectId, sidebarMode, atHome]);

  // activeAgentId -> /agents/<uuid> (or /agents when nothing is selected). The
  // canonical URL uses the public UUID; depends on `agents` so the link is
  // rewritten from a transient integer fallback once the list resolves.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "agents") return;
    // Don't clobber a deep-link URL whose agent we haven't resolved yet (the
    // list may still be loading). Overwriting it with "/agents" here would also
    // drop the UUID before the agents-load effect can resolve it.
    if (activeAgentId == null && pendingAgentRef.current) return;
    const target = agentUrl(activeAgentId, agents);
    if (window.location.pathname !== target) history.pushState(null, "", target);
  }, [activeAgentId, sidebarMode, agents, atHome]);

  // Auto-clear the search highlight when the user navigates to a *different*
  // conversation than the one it was opened for. The highlight is tied to a
  // single conversation (`${mode}:${id}`): while a search-open is in flight we
  // wait until the selection lands on its target; a URL-loaded term adopts the
  // first conversation it resolves to; any later switch to a different one
  // clears it. Transitional states with no complete selection are ignored.
  useEffect(() => {
    if (!searchHighlight.trim()) return;
    let key: string | null = null;
    if (sidebarMode === "topics") key = activeTopic ? `topics:${activeTopic.id}` : null;
    else if (sidebarMode === "chats") key = activeChat ? `chats:${activeChat.id}` : null;
    else if (sidebarMode === "agents")
      key = activeAgentId != null ? `agents:${activeAgentId}` : null;
    else if (sidebarMode === "live")
      key = activeSessionId != null ? `live:${activeSessionId}` : null;
    if (key == null) return; // mid-transition — wait for a complete selection
    const pending = pendingHighlightKeyRef.current;
    if (pending != null) {
      // Still travelling to the just-opened search target; adopt once we arrive.
      if (key === pending) {
        highlightKeyRef.current = pending;
        pendingHighlightKeyRef.current = null;
      }
      return;
    }
    if (highlightKeyRef.current == null) {
      highlightKeyRef.current = key; // first resolved conversation owns the term
      return;
    }
    if (key !== highlightKeyRef.current) {
      setSearchHighlight("");
      highlightKeyRef.current = null;
    }
  }, [searchHighlight, sidebarMode, activeTopic, activeChat, activeAgentId, activeSessionId]);

  // Mirror the highlight term into `?q=` on the current path so a reloaded or
  // shared link re-highlights. Runs after the pathname effects above (which
  // pushState a path without a query), re-appending `q` via replaceState.
  // Depends on the selection state so it re-fires after each navigation. The
  // query is dropped at home, where there's no conversation to highlight.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const cur = params.get("q") ?? "";
    const want = atHome ? "" : searchHighlight.trim();
    if (cur === want) return;
    if (want) params.set("q", want);
    else params.delete("q");
    const qs = params.toString();
    history.replaceState(null, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }, [
    searchHighlight,
    atHome,
    sidebarMode,
    activeTopic,
    activeChat,
    activeAgentId,
    activeSessionId,
  ]);

  // Bring the first highlighted match into view once the opened conversation has
  // rendered. Messages load asynchronously, so poll briefly until a match
  // appears (or give up after ~2.5s).
  useEffect(() => {
    if (!searchHighlight.trim() || atHome) return;
    let tries = 0;
    const id = window.setInterval(() => {
      const el = document.querySelector(".search-hl");
      if (el) {
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        window.clearInterval(id);
      } else if (++tries > 20) {
        window.clearInterval(id);
      }
    }, 120);
    return () => window.clearInterval(id);
  }, [
    searchHighlight,
    atHome,
    activeTopic,
    activeChat,
    activeAgentId,
    activeSessionId,
  ]);


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
    // Clicking the active mode's tab while on the home launcher still needs to
    // leave home, so only short-circuit when we're already showing that mode.
    if (next === sidebarMode && !atHome) return;
    setAtHome(false);
    let target = "/topics";
    if (next === "topics") {
      target = activeTopicRef.current
        ? topicUrl(treeRef.current, activeTopicRef.current)
        : "/topics";
    } else if (next === "chats") {
      target = activeChatRef.current ? chatUrl(activeChatRef.current) : "/chats";
    } else if (next === "live") {
      // Entering the Live section lands on the create surface (like starting a
      // fresh topic/agent): drop the selection so the "new session" hero shows.
      // Existing sessions stay one click away in the list.
      setActiveSessionId(null);
      target = "/live";
    } else if (next === "agents") {
      target = agentUrl(activeAgentIdRef.current, agentsRef.current);
    } else if (next === "kanban") {
      const active =
        projectsRef.current?.find((p) => p.id === activeProjectIdRef.current) ?? null;
      target = kanbanUrl(active);
    } else {
      target = "/ws";
    }
    history.pushState(null, "", target);
    setWsRoute(next === "workspaces" ? parseWsRoute() : { open: false, slug: null, path: null });
    setSidebarMode(next);
  }

  // Navigate to the root home launcher.
  function goHome(): void {
    if (window.location.pathname !== "/") history.pushState(null, "", "/");
    setWsRoute({ open: false, slug: null, path: null });
    setAtHome(true);
  }

  // The sidebar "+" (mode-aware) leaves the home launcher and drops into a
  // mode's start surface. The home cards themselves stay on `/` and reveal the
  // start surface inline (see the home*FromHome handlers below).
  function startNewFromHome(mode: SidebarMode): void {
    setAtHome(false);
    setWsRoute({ open: false, slug: null, path: null });
    if (mode === "topics") {
      setActiveTopic(null);
      setTopicDraftParentId(null);
      setTopicDraftNonce((n) => n + 1);
      history.pushState(null, "", "/topics");
      setSidebarMode("topics");
    } else if (mode === "chats") {
      setActiveChat(null);
      history.pushState(null, "", "/chats");
      setSidebarMode("chats");
    } else if (mode === "live") {
      setActiveSessionId(null);
      history.pushState(null, "", "/live");
      setSidebarMode("live");
    } else if (mode === "agents") {
      setActiveAgentId(null);
      history.pushState(null, "", "/agents");
      setSidebarMode("agents");
    } else {
      history.pushState(null, "", "/ws");
      setSidebarMode("workspaces");
      setWsRoute(parseWsRoute());
      setCreateWorkspaceOpen(true);
    }
  }

  // ---- Home launcher inline surfaces -----------------------------------
  // Each home card reveals a start surface in place; on successful creation the
  // handlers below leave home and route to the freshly-created item.

  // A topic was created (from the home launcher or the Topics create form):
  // leave home, clear the draft parent, and reveal the new topic.
  async function handleTopicCreated(topic: Topic): Promise<void> {
    setTopicDraftParentId(null);
    setAtHome(false);
    setSidebarMode("topics");
    await refreshTree();
    setActiveTopic(topic);
  }

  // The "New chat" card's inline composer: create + stream, then reveal the chat.
  async function startChatFromHome(prompt: string): Promise<void> {
    setAtHome(false);
    setSidebarMode("chats");
    await handleStartChat(prompt);
  }

  // The "New live session" card's inline form: create, then reveal the session.
  async function createLiveFromHome(session: MeetingSession): Promise<void> {
    setAtHome(false);
    setSidebarMode("live");
    await loadMeetingSessions();
    setActiveSessionId(session.id);
    history.pushState(null, "", liveUrl(session));
  }

  // The "New agent" card's inline start form calls this once the agent exists.
  function selectAgentFromHome(id: number | null): void {
    if (id == null) return;
    setAtHome(false);
    setSidebarMode("agents");
    setActiveAgentId(id);
  }

  // Global ⌘K / Ctrl+K toggles the command palette — a width-independent way to
  // jump to any section regardless of the sidebar's horizontal overflow.
  useEffect(() => {
    function onKey(e: KeyboardEvent): void {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // If the Live section gets disabled while it's open (or a deep link lands on
  // it while disabled), fall back to Topics.
  useEffect(() => {
    if (!liveEnabled && sidebarMode === "live") {
      history.pushState(null, "", "/topics");
      setSidebarMode("topics");
    }
  }, [liveEnabled, sidebarMode]);

  // Same guard for the kanban board: if the repo/issue settings that gate it get
  // turned off (or a deep link lands while disabled), fall back to Topics. Wait
  // for settings to load first — otherwise a deep link to /kanban is bounced to
  // /topics before `kanbanEnabled` resolves from the initial null settings.
  useEffect(() => {
    if (settings == null) return;
    if (!kanbanEnabled && sidebarMode === "kanban") {
      history.pushState(null, "", "/topics");
      setSidebarMode("topics");
    }
  }, [kanbanEnabled, sidebarMode, settings]);

  // Lazily load projects the first time the user enters kanban mode. Re-runs if
  // the configured repo changes (projects reset to null by that handler).
  useEffect(() => {
    if (sidebarMode !== "kanban" || !kanbanEnabled || projects !== null) return;
    setProjectsError(null);
    void api.github
      .listProjects()
      .then((list) => {
        setProjects(list);
        // Resolve a deep-linked project ref (a number/slug) to its node id;
        // otherwise auto-select the first project.
        const num = projectRefNumber(pendingProjectRef.current);
        const fromRef = num != null ? list.find((p) => p.number === num) : undefined;
        pendingProjectRef.current = null;
        setActiveProjectId((id) => id ?? fromRef?.id ?? list[0]?.id ?? null);
      })
      .catch((e) => {
        setProjects([]);
        setProjectsError(apiErrorMessage(e, "Failed to load projects"));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode, kanbanEnabled]);

  // When the configured repo changes, drop the cached project list + selection
  // so the next kanban visit reloads for the new repo. Skips the initial
  // settings load so a deep-linked project id isn't wiped before it resolves.
  const prevRepoRef = useRef<string | null>(null);
  useEffect(() => {
    if (settings == null) return;
    if (prevRepoRef.current === null) {
      prevRepoRef.current = globalGithubRepo;
      return;
    }
    if (prevRepoRef.current === globalGithubRepo) return;
    prevRepoRef.current = globalGithubRepo;
    setProjects(null);
    setActiveProjectId(null);
    setProjectsError(null);
  }, [globalGithubRepo, settings]);

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
          if (isViewing("chat", id) && windowFocused()) {
            try {
              await api.chats.markRead(id);
            } catch {
              // non-fatal
            }
          }
          setChatListReloadKey((k) => k + 1);
          void refreshChatsUnread();
          return;
        }
        if (isViewing("topic", id) && windowFocused()) {
          try {
            await api.topics.markRead(id);
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
              const refreshed = await api.topics.get(active.id);
              setActiveTopic(refreshed);
            } catch {
              // topic may have been deleted in another window; ignore
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
          return;
        }
        const topicId = event.topic_id;
        const topicActive = topicId != null && isViewing("topic", topicId);
        // Sidebar badge tracking depends on the tree, so always refresh — and
        // keep the actively-viewed topic read for the same reason as chats.
        void (async () => {
          if (topicActive && windowFocused()) {
            try {
              await api.topics.markRead(topicId);
            } catch {
              // non-fatal
            }
          }
          await refreshTree();
        })();
        if (topicActive) {
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
          void refreshChatsUnread();
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
      } else if (event.type === "read.changed") {
        // Another tab marked a conversation read. Refetch only the affected
        // section's unread state so this tab's badge + counter clear in sync.
        // This never re-marks anything, so it can't loop with the active-view
        // read logic.
        if (event.chat_id != null) {
          setChatListReloadKey((k) => k + 1);
          void refreshChatsUnread();
        } else if (event.agent_session_id != null) {
          void loadAgents();
        } else {
          void refreshTree();
        }
      } else if (event.type === "agent.changed") {
        // An agent session was created, advanced, or finished (possibly in the
        // background). Refresh the list so statuses/badges stay current; the
        // AgentView refreshes its own timeline.
        void (async () => {
          const list = await loadAgents();
          // Keep the session the user is actively viewing marked read as it
          // produces output, so its badge doesn't resurrect when they navigate
          // away (mirrors how a chat/topic is re-marked read on turn completion).
          // Gate on a settled status so we mark once per turn, not per streamed
          // event; markAgentRead doesn't publish, so this can't loop.
          const activeId = activeAgentIdRef.current;
          if (activeId == null || !isViewing("agent", activeId) || !windowFocused()) return;
          const active = list.find((a) => a.id === activeId);
          if (active && active.unread_count > 0 && AGENT_SETTLED_STATUSES.has(active.status)) {
            try {
              await api.agents.markRead(activeId);
              await loadAgents();
            } catch {
              // non-fatal
            }
          }
        })();
      } else if (event.type === "meeting.changed") {
        // A meeting session was created, renamed, ended, or deleted (possibly in
        // another tab). Refresh the list if we've loaded it so the Live section
        // stays current.
        if (meetingSessionsRef.current !== null) void loadMeetingSessions();
      }
    });
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
            setChatListReloadKey((k) => k + 1);
            void refreshChatsUnread();
          } else if (v.kind === "topic") {
            await api.topics.markRead(v.id);
            await refreshTree();
          } else {
            await api.agents.markRead(v.id);
            await loadAgents();
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

  async function handleSelect(id: number): Promise<void> {
    setActiveTopic(await api.topics.get(id));
    try {      await api.topics.markRead(id);
      await refreshTree();
    } catch {
      // non-fatal
    }
  }

  // Inline rename from the sidebar tree (double-click a topic's name).
  async function handleRenameTopic(id: number, title: string): Promise<void> {
    const updated = await api.topics.update(id, { title });
    if (activeTopicRef.current?.id === id) setActiveTopic(updated);
    await refreshTree();
  }

  async function handleRenameAgent(id: number, title: string): Promise<void> {
    await api.agents.rename(id, title);
    await loadAgents();
  }

  async function handleArchiveAgents(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.agents.archive(id)));
    if (activeAgentId != null && ids.includes(activeAgentId)) setActiveAgentId(null);
    await loadAgents();
  }

  async function handleStopAgent(id: number): Promise<void> {
    await api.agents.cancel(id);
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
    await api.agents.remove(agent.id);
    if (activeAgentId === agent.id) setActiveAgentId(null);
    await loadAgents();
  }

  async function handleSelectChat(chat: Chat): Promise<void> {
    setActiveChat(chat);
    try {
      await api.chats.markRead(chat.id);
      setChatListReloadKey((k) => k + 1);
    } catch {
      // non-fatal
    }
  }

  // Open a content-search hit from the command palette. Mirrors the per-section
  // deep-link resolution: leave home, switch mode, and reveal the entity by its
  // stable id (topic/chat/live-session row id, or agent internal id).
  async function openSearchResult(result: SearchResult, query: string): Promise<void> {
    setAtHome(false);
    // Carry the matched term into the opened view so its bodies get highlighted;
    // the ?q= URL sync effect mirrors it for shareable/reloadable links. Record
    // the target conversation so the navigation-away auto-clear waits until we
    // actually land on it instead of clearing during the transition.
    pendingHighlightKeyRef.current = `${result.section}:${result.entity_id}`;
    highlightKeyRef.current = null;
    setSearchHighlight(query.trim());
    try {
      if (result.section === "topics") {
        setSidebarMode("topics");
        await handleSelect(result.entity_id);
      } else if (result.section === "chats") {
        setSidebarMode("chats");
        await handleSelectChat(await api.chats.get(result.entity_id));
      } else if (result.section === "agents") {
        setSidebarMode("agents");
        setActiveAgentId(result.entity_id);
      } else if (result.section === "live") {
        setSidebarMode("live");
        // Ensure the session list is loaded so the URL-sync effect can resolve
        // the slug once we select it.
        if (!meetingSessionsRef.current?.some((s) => s.id === result.entity_id)) {
          await loadMeetingSessions();
        }
        setActiveSessionId(result.entity_id);
      }
    } catch {
      // Entity may have been deleted since the search — leave the user in the
      // section they landed on rather than surfacing an error.
    }
  }

  async function handleArchiveChats(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.chats.archive(id)));
    if (activeChat && ids.includes(activeChat.id)) setActiveChat(null);
    setChatListReloadKey((k) => k + 1);
  }

  // Open the conversation behind a fired reminder, switching mode if needed.
  async function handleReminderSelect(item: ReminderItem): Promise<void> {
    try {
      if (item.container === "topic" && item.topic_id != null) {
        changeMode("topics");
        await handleSelect(item.topic_id);
      } else if (item.container === "chat" && item.chat_id != null) {
        changeMode("chats");
        await handleSelectChat(await api.chats.get(item.chat_id));
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
      await api.reminders.clear(item.container, id);
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
  // switch to the newly-mounted ChatSessionPanel.
  async function handleStartChat(prompt: string): Promise<void> {
    const text = prompt.trim();
    if (!text) return;
    const chat = await api.chats.create({ title: "New chat" });
    setActiveChat(chat);
    setChatListReloadKey((k) => k + 1);
    void streamStore.start(convKey("chat", chat.id), text);
  }

  // The sidebar header's single "New" button adapts to the active mode so the
  // create affordance lives in the same place across Topics / Chats / Files.
  // Chats and agents drop the selection to reveal their "start" landing surface;
  // topics open the create dialog directly.
  function handleNew(): void {
    // The "+" always lands on a mode's create surface, so leave the home
    // launcher (routing to the current mode's start surface) if we're on it.
    if (atHome) {
      startNewFromHome(sidebarMode);
      return;
    }
    if (sidebarMode === "topics") handleCreate(null);
    else if (sidebarMode === "chats") setActiveChat(null);
    else if (sidebarMode === "live") setActiveSessionId(null);
    else if (sidebarMode === "agents") setActiveAgentId(null);
    else if (sidebarMode === "kanban") {
      // No "new" affordance for the kanban board (the header hides the "+").
    } else setCreateWorkspaceOpen(true);
  }

  // ---- Workspaces -------------------------------------------------------
  const activeWorkspace =
    workspaces?.find((w) => w.id === activeWorkspaceId) ?? null;
  // The route path only applies to the workspace named in the URL.
  const workspaceInitialPath =
    activeWorkspace && activeWorkspace.slug === wsRoute.slug ? wsRoute.path : null;

  // ---- Live meeting sessions -------------------------------------------
  const activeSession =
    meetingSessions?.find((s) => s.id === activeSessionId) ?? null;

  // ---- Assistant roles --------------------------------------------------
  // The header role selector and the `/role` command both funnel through here.
  // Selecting the default role persists null (which resolves to default
  // server-side). Updates are persisted per-discussion and reflected locally.
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (sidebarMode === "topics" && activeTopic) {
      const updated = await api.topics.update(activeTopic.id, { role_id: roleId });
      if (activeTopicRef.current?.id === activeTopic.id) setActiveTopic(updated);
    } else if (sidebarMode === "chats" && activeChat) {
      const updated = await api.chats.update(activeChat.id, { role_id: roleId });
      if (activeChatRef.current?.id === activeChat.id) setActiveChat(updated);
    } else if (sidebarMode === "workspaces" && activeWorkspace) {
      const updated = await api.workspaces.update(activeWorkspace.id, {
        role_id: roleId,
      });
      setWorkspaces((prev) =>
        prev ? prev.map((w) => (w.id === updated.id ? updated : w)) : prev,
      );
    } else if (sidebarMode === "live" && activeSession) {
      const updated = await api.meetings.updateSession(activeSession.id, { role_id: roleId });
      setMeetingSessions((prev) =>
        prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
      );
    } else if (sidebarMode === "agents" && activeAgent) {
      const updated = await api.agents.update(activeAgent.id, { role_id: roleId });
      setAgents((prev) =>
        prev ? prev.map((a) => (a.id === updated.id ? updated : a)) : prev,
      );
    }
  }

  const activeRoleId =
    sidebarMode === "topics"
      ? (activeTopic?.role_id ?? null)
      : sidebarMode === "chats"
        ? (activeChat?.role_id ?? null)
        : sidebarMode === "workspaces"
          ? (activeWorkspace?.role_id ?? null)
          : sidebarMode === "live"
            ? (activeSession?.role_id ?? null)
            : sidebarMode === "agents"
              ? (activeAgent?.role_id ?? null)
              : null;

  const hasActiveDiscussion =
    (sidebarMode === "topics" && !!activeTopic) ||
    (sidebarMode === "chats" && !!activeChat) ||
    (sidebarMode === "workspaces" && !!activeWorkspace) ||
    (sidebarMode === "live" && !!activeSession) ||
    (sidebarMode === "agents" && !!activeAgent);

  async function loadWorkspaces(): Promise<Workspace[]> {
    const list = await api.workspaces.list();
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

  // ---- Live meeting sessions --------------------------------------------
  async function loadMeetingSessions(): Promise<MeetingSession[]> {
    const list = await api.meetings.listSessions();
    setMeetingSessions(list);
    return list;
  }

  // Lazily load sessions the first time the user enters live mode, then pick an
  // active one (honouring a slug from the URL, else the first).
  useEffect(() => {
    if (sidebarMode !== "live" || meetingSessions !== null) return;
    const { liveSlug } = parseAppRoute();
    void loadMeetingSessions().then((list) => {
      if (list.length === 0) return;
      const fromRoute = liveSlug ? list.find((s) => s.slug === liveSlug) : undefined;
      // Only auto-select when the URL points at a specific session; otherwise
      // leave nothing selected so the start hero shows.
      if (fromRoute) setActiveSessionId((id) => id ?? fromRoute.id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sidebarMode]);

  function handleSelectSession(session: MeetingSession): void {
    setActiveSessionId(session.id);
    history.pushState(null, "", liveUrl(session));
  }

  async function handleRenameSession(session: MeetingSession, title: string): Promise<void> {
    const updated = await api.meetings.updateSession(session.id, { title });
    setMeetingSessions((prev) =>
      prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
    );
  }

  async function handleArchiveSessions(ids: number[]): Promise<void> {
    await Promise.all(ids.map((id) => api.meetings.archiveSession(id)));
    if (activeSessionId != null && ids.includes(activeSessionId)) setActiveSessionId(null);
    await loadMeetingSessions();
  }

  // ---- Agents -----------------------------------------------------------
  async function loadAgents(): Promise<AgentSession[]> {
    try {
      const list = await api.agents.list();
      // Notify for sessions whose unread grew since the last load — i.e. a
      // background/scheduled agent produced a new reply — skipping the very
      // first load and whichever session is currently open. Mirrors how a
      // finished topic turn notifies (see maybeNotify).
      const prev = agentUnreadRef.current;
      if (prev && notificationsEnabledRef.current) {
        for (const a of list) {
          const before = prev.get(a.id) ?? 0;
          if (a.unread_count > before && a.id !== activeAgentIdRef.current) {
            notifyIfUnfocused({
              title: a.title,
              body: "Agent has a new update.",
              tag: `precursor-agent-${a.id}`,
            });
          }
        }
      }
      agentUnreadRef.current = new Map(list.map((a) => [a.id, a.unread_count ?? 0]));
      setAgents(list);
      return list;
    } catch {
      setAgents([]);
      return [];
    }
  }

  // Mark the active agent read (and refresh badges) whenever it changes to a
  // real session — covers list clicks, the AgentView, deep links and route
  // sync in one place. markAgentRead doesn't publish, so this can't loop.
  useEffect(() => {
    if (activeAgentId == null) return;
    void api.agents.markRead(activeAgentId)
      .then(() => loadAgents())
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAgentId]);

  // Load agent sessions as soon as the feature is known-enabled (independent of
  // the current mode) so the mode-switcher badge and background completion
  // notifications work from anywhere, not just inside agents mode.
  useEffect(() => {
    if (!agentsEnabled || agents !== null) return;
    void loadAgents();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentsEnabled]);

  // Reveal the inline "new topic" form in the main pane (the Topics empty
  // state). Top-level "+ create" passes null; if a topic is selected the new one
  // nests under it. Per-node "+ child" buttons pass their own id explicitly.
  function handleCreate(parentId: number | null): void {
    const parent = parentId ?? activeTopic?.id ?? null;
    setAtHome(false);
    setSidebarMode("topics");
    setTopicDraftParentId(parent);
    setTopicDraftNonce((n) => n + 1);
    setActiveTopic(null);
    if (window.location.pathname !== "/topics") {
      history.pushState(null, "", "/topics");
    }
  }

  function openTopicSettings(tab: "settings" | "context" = "settings"): void {
    setTopicSettingsTab(tab);
    setTopicSettingsOpen(true);
  }

  async function togglePin(): Promise<void> {
    if (!activeTopic) return;
    const updated = await api.topics.update(activeTopic.id, {
      pinned: !activeTopic.pinned,
    });
    setActiveTopic(updated);
    await refreshTree();
  }

  return (
    <div
      className={`flex h-full w-full bg-bg text-text${
        atHome ? "" : ` section-${sidebarMode}`
      }`}
    >
      <TooltipProvider />
      <DetachedDraftHost />
      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onNavigate={changeMode}
          onGoHome={goHome}
          onOpenResult={openSearchResult}
          liveEnabled={liveEnabled}
          kanbanEnabled={kanbanEnabled}
          initialQuery={atHome ? "" : searchHighlight.trim()}
        />
      )}
      {atHome && navStyle === "rail" && (
        <SectionRail
          mode={sidebarMode}
          atHome
          onGoHome={goHome}
          onOpenPalette={() => setPaletteOpen(true)}
          onModeChange={changeMode}
          onNew={handleNew}
          unreadByMode={unreadByMode}
          liveEnabled={liveEnabled}
          kanbanEnabled={kanbanEnabled}
        />
      )}
      {!atHome && (
      <Sidebar
        tree={tree}
        activeId={activeTopic?.id ?? null}
        streamingTopicIds={streamingTopicIds}
        collapsed={sidebarCollapsed}
        mode={sidebarMode}
        onModeChange={changeMode}
        atHome={atHome}
        onGoHome={goHome}
        onOpenPalette={() => setPaletteOpen(true)}
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
            onUnreadChange={setChatsUnread}
            onArchiveMany={handleArchiveChats}
          />
        }
        workspaceSlot={
          <WorkspaceList
            workspaces={workspaces}
            activeId={activeWorkspaceId}
            onSelect={handleSelectWorkspace}
          />
        }
        liveSlot={
          <LiveList
            sessions={meetingSessions}
            activeId={activeSessionId}
            recordingId={liveRecordingId}
            onSelect={handleSelectSession}
            onRename={handleRenameSession}
            onArchiveMany={handleArchiveSessions}
          />
        }
        agentSlot={
          <AgentList
            agents={agents ?? []}
            activeId={activeAgentId}
            enabled={agentsEnabled}
            onSelect={(id) => setActiveAgentId(id)}
            onRename={handleRenameAgent}
            onArchiveMany={handleArchiveAgents}
          />
        }
        kanbanSlot={
          <ProjectList
            projects={projects}
            activeId={activeProjectId}
            error={projectsError}
            onSelect={(p) => {
              pendingProjectRef.current = null;
              // Switching boards drops any hash-selected card from the old one.
              setKanbanItemNumber(null);
              setActiveProjectId(p.id);
            }}
          />
        }
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        onSelect={handleSelect}
        onNew={handleNew}
        onCreate={handleCreate}
        onRename={handleRenameTopic}
        liveEnabled={liveEnabled}
        reminders={reminders}
        reminderTopicIds={reminderTopicIds}
        onReminderSelect={handleReminderSelect}
        onReminderDone={handleReminderDone}
        onRefresh={refreshTree}
        onOpenGlobalSettings={() => setGlobalSettingsOpen(true)}
        onOpenArchive={() => setArchiveOpen(true)}
        unreadByMode={unreadByMode}
        kanbanEnabled={kanbanEnabled}
      />
      )}

      <main className="flex-1 flex flex-col min-w-0">
        {/* One shared header across every mode: active item title on the left,
            mode-specific actions on the right. */}
        <header className="flex items-center justify-between px-4 h-12 border-b border-border gap-3">
          {atHome ? (
            <span className="truncate font-medium min-w-0 flex-1">Home</span>
          ) : sidebarMode === "topics" ? (
            <>
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {activeTopic ? (
                  <>
                    {topicAncestors(tree, activeTopic.id).map((anc) => (
                      <div key={anc.id} className="flex items-center gap-2 min-w-0 shrink">
                        <button
                          type="button"
                          onClick={() => void handleSelect(anc.id)}
                          className="max-w-[10rem] truncate text-sm text-muted hover:text-fg hover:underline"
                          title={anc.title}
                          data-tooltip={`Go to ${anc.title}`}
                        >
                          {anc.title}
                        </button>
                        <ChevronRight size={14} className="shrink-0 text-muted" />
                      </div>
                    ))}
                    <InlineTitle
                      title={activeTopic.title}
                      onRename={(t) => handleRenameTopic(activeTopic.id, t)}
                      className="truncate font-medium"
                      inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
                    />
                  </>
                ) : (
                  <span className="truncate font-medium">Select or create a topic</span>
                )}
                {activeTopic && issueAssociationsEnabled && (
                  <IssueStatusBadge
                    status={issueContext.status}
                    onClick={() => openTopicSettings("context")}
                  />
                )}
              </div>
              {activeTopic &&
                issueAssociationsEnabled &&
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
          ) : sidebarMode === "live" ? (
            activeSession ? (
              (() => {
                const liveTopic =
                  activeSession.topic_id != null
                    ? findNode(tree, activeSession.topic_id)
                    : null;
                const liveIssueNumber = liveTopic?.github_issue_number ?? null;
                const liveIssueRepo =
                  liveTopic?.github_repo || globalGithubRepo || "";
                return (
                  <>
                    <InlineTitle
                      title={activeSession.title}
                      onRename={(t) => handleRenameSession(activeSession, t)}
                      className="truncate font-medium min-w-0 flex-1"
                      inputClassName="min-w-0 flex-1 rounded border border-accent/60 bg-bg px-1.5 py-0.5 text-sm font-medium outline-none"
                    />
                    {liveTopic && (
                      <button
                        type="button"
                        onClick={() => {
                          changeMode("topics");
                          void handleSelect(liveTopic.id);
                        }}
                        className="p-2 rounded text-sky-600 hover:bg-surface shrink-0 dark:text-sky-400"
                        aria-label={`Open topic: ${liveTopic.title}`}
                        data-tooltip={`Open topic: ${liveTopic.title}`}
                      >
                        <MessagesSquare size={18} />
                      </button>
                    )}
                    {liveIssueNumber != null && liveIssueRepo && (
                      <a
                        href={`https://github.com/${liveIssueRepo}/issues/${liveIssueNumber}`}
                        target="_blank"
                        rel="noreferrer"
                        className="group inline-flex items-center gap-1 p-2 rounded hover:bg-surface shrink-0"
                        aria-label={`Open issue #${liveIssueNumber} on GitHub`}
                        data-tooltip={`Open issue #${liveIssueNumber} on GitHub`}
                      >
                        <Github size={18} />
                        <ExternalLink
                          size={11}
                          className="opacity-60 transition group-hover:opacity-100"
                        />
                      </a>
                    )}
                  </>
                );
              })()
            ) : (
              <span className="truncate font-medium min-w-0 flex-1">Live</span>
            )
          ) : sidebarMode === "kanban" ? (
            <span className="truncate font-medium min-w-0 flex-1">
              {projects?.find((p) => p.id === activeProjectId)?.title ?? "Kanban"}
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
          {!atHome && hasActiveDiscussion && (
            <RoleSelector
              value={activeRoleId}
              onChange={(roleId) => void setRoleForActive(roleId)}
              open={roleSelectorOpen}
              onOpenChange={setRoleSelectorOpen}
            />
          )}
        </header>

        <McpAuthBanner />

        {searchHighlight.trim() && !atHome && (
          <div className="flex items-center justify-center gap-2 border-b border-border bg-accent/5 px-3 py-1 text-[11px] text-muted">
            <Search size={12} className="shrink-0" />
            <span>
              Highlighting{" "}
              <span className="font-medium text-text">“{searchHighlight.trim()}”</span>
            </span>
            <button
              type="button"
              onClick={() => setSearchHighlight("")}
              className="ml-1 inline-flex items-center gap-0.5 rounded px-1 py-0.5 hover:text-red-500"
              aria-label="Clear highlight"
              data-tooltip="Clear highlight"
            >
              <X size={12} />
              Clear
            </button>
          </div>
        )}

        <div className="flex-1 min-h-0">
          <SearchHighlightProvider term={atHome ? "" : searchHighlight.trim()}>
          {atHome ? (
            <HomePage
              liveEnabled={liveEnabled}
              kanbanEnabled={kanbanEnabled}
              onNavigate={changeMode}
              onOpenSettings={() => setGlobalSettingsOpen(true)}
              onOpenArchive={() => setArchiveOpen(true)}
              topicSurface={
                <TopicStartHero tree={tree} onCreated={handleTopicCreated} />
              }
              chatSurface={<ChatStartHero onStart={startChatFromHome} />}
              liveSurface={
                <LiveStartHero topics={tree} onCreated={createLiveFromHome} />
              }
              agentSurface={
                <AgentView
                  agents={agents ?? []}
                  agentId={null}
                  enabled={agentsEnabled}
                  available={agentsAvailable}
                  unavailableReason={agentsUnavailableReason}
                  onReload={() => void loadAgents()}
                  onSelect={selectAgentFromHome}
                  onOpenSettings={() => setGlobalSettingsOpen(true)}
                  draftTopicId={null}
                />
              }
            />
          ) : sidebarMode === "topics" ? (
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
                    await api.topics.markRead(activeTopic.id);
                  } catch {
                    // non-fatal
                  }
                  try {
                    setActiveTopic(await api.topics.get(activeTopic.id));
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
              <TopicStartHero
                key={`topic-create-${topicDraftParentId ?? "root"}-${topicDraftNonce}`}
                tree={tree}
                initialParentId={topicDraftParentId}
                onCreated={handleTopicCreated}
              />
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
              <ChatStartHero onStart={handleStartChat} />
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
          ) : sidebarMode === "live" ? (
            activeSession ? (
              <LiveView
                key={activeSession.id}
                session={activeSession}
                topics={tree}
                onUpdated={(updated) =>
                  setMeetingSessions((prev) =>
                    prev ? prev.map((s) => (s.id === updated.id ? updated : s)) : prev,
                  )
                }
                onDeleted={async () => {
                  const list = await loadMeetingSessions();
                  setActiveSessionId(null);
                  history.pushState(null, "", liveUrl(null));
                  void list;
                }}
                onArchived={async () => {
                  await loadMeetingSessions();
                  setActiveSessionId(null);
                  history.pushState(null, "", liveUrl(null));
                }}
                onRecordingChange={setLiveRecordingId}
              />
            ) : (
              <LiveStartHero
                topics={tree}
                onCreated={async (session) => {
                  await loadMeetingSessions();
                  setActiveSessionId(session.id);
                  history.pushState(null, "", liveUrl(session));
                }}
              />
            )
          ) : sidebarMode === "kanban" ? (
            projects === null ? (
              <EmptyHero label="Loading projects…" />
            ) : projectsError ? (
              <EmptyHero label={projectsError} />
            ) : activeProjectId ? (
              <KanbanBoard
                key={activeProjectId}
                projectId={activeProjectId}
                fallbackRepo={globalGithubRepo}
                selectedNumber={kanbanItemNumber}
                onSelectedNumberChange={setKanbanItemNumber}
                onOpenTopic={(topicId) => {
                  changeMode("topics");
                  void handleSelect(topicId);
                }}
              />
            ) : projects.length === 0 ? (
              <EmptyHero label="No GitHub projects found for this repository." />
            ) : (
              <EmptyHero label="Select a project to view its board." />
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
          </SearchHighlightProvider>
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
          onSessionRestored={() => void loadMeetingSessions()}
          onSessionDeleted={(id) => {
            if (activeSessionId === id) setActiveSessionId(null);
            void loadMeetingSessions();
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

