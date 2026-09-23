import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronRight,
  FileText,
  Menu,
  Pin,
  PinOff,
  Search,
  Settings as SettingsIcon,
  X,
} from "lucide-react";
import { Sidebar, SectionRail, type SidebarMode } from "./components/Sidebar";
import { resolveSections } from "./lib/plugins";
import { usePluginDescriptors } from "./lib/pluginStore";
import type { SectionHost } from "./lib/plugins";
import { CommandPalette } from "./components/CommandPalette";
import { ChatPanel } from "./components/ChatPanel";
import { McpAuthBanner } from "./components/McpAuthBanner";
import { SettingsPanel, pluginSettingsTab } from "./components/SettingsPanel";
import { TopicSettingsPanel } from "./components/TopicSettingsPanel";
import { TopicStartHero } from "./components/StartHero";
import { HomePage } from "./components/HomePage";
import { ArchivePanel } from "./components/ArchivePanel";
import { IssueStatusBadge } from "./components/IssueStatusBadge";
import { IssueLabelChip, IssueStateBadge } from "./components/IssueTags";
import {
  LiveHeader,
  LiveHomeSurface,
  LiveMain,
  LiveSidebarList,
} from "./components/LiveSectionParts";
import {
  AgentRunErrorBanner,
  AgentSettingsModal,
  AgentsHeader,
  AgentsHomeSurface,
  AgentsMain,
  AgentsSidebarList,
} from "./components/AgentsSection";
import {
  ChatsHeader,
  ChatsHomeSurface,
  ChatsMain,
  ChatsSettingsModal,
  ChatsSidebarList,
} from "./components/ChatsSectionParts";
import {
  WorkflowsHeader,
  WorkflowsMain,
  WorkflowsSidebarSlot,
} from "./components/WorkflowsSectionParts";
import {
  WorkspacesCreateModal,
  WorkspacesHeader,
  WorkspacesMain,
  WorkspacesSidebarList,
} from "./components/WorkspacesSectionParts";
import { PersonaMenu } from "./components/PersonaMenu";
import { DetachedDraftHost } from "./components/DetachedDraftHost";
import { InlineTitle } from "./components/InlineTitle";
import { toggleTopicSummary } from "./lib/summaryOpen";
import { useConfirm } from "./components/ConfirmDialog";
import { TooltipProvider } from "./components/Tooltip";
import { ReminderModal } from "./components/ReminderModal";
import { api } from "./lib/api";
import { Z_INDEX } from "./lib/constants";
import { SearchHighlightProvider } from "./lib/searchHighlight";
import { eventBus } from "./lib/events";
import { notifyIfUnfocused } from "./lib/notifications";
import { skillsStore } from "./lib/skillsStore";
import { rolesStore } from "./lib/rolesStore";
import { useSettings, useSettingsReady } from "./lib/settingsStore";
import { streamStore, useStreamVersion, convKey } from "./lib/streamStore";
import { useIssueContext } from "./lib/useIssueContext";
import { useGlobalShortcuts } from "./lib/useGlobalShortcuts";
import { useIsNarrow } from "./lib/useMediaQuery";
import { useSidebarNavStyle } from "./lib/useSidebarNavStyle";
import { useAgentsController } from "./lib/useAgentsController";
import { useChatsController } from "./lib/useChatsController";
import {
  useLiveSessionsController,
  useLiveSessionsLateEffects,
} from "./lib/useLiveSessionsController";
import { useWorkflowsController } from "./lib/useWorkflowsController";
import { useWorkspacesController } from "./lib/useWorkspacesController";
import { windowFocused } from "./lib/windowFocus";
import { openNotes } from "./lib/notesOpen";
import type {
  Collection,
  ReminderItem,
  SearchResult,
  Topic,
  TopicNode,
} from "./lib/types";
import {
  pickInitialCollection,
  readStoredCollectionId,
  writeStoredCollectionId,
} from "./lib/collections";
import {
  isHomePath,
  isPluginMode,
  navigate,
  parseAppRoute,
  pluginSectionUrl,
  searchTermFromUrl,
  topicsModeUrl,
  topicUrl,
  type PluginRoute,
} from "./lib/routes";
import { findTitle, topicAncestors, totalUnread } from "./lib/topicTree";

const BASE_TITLE = "Precursor";

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
  const [searchHighlight, setSearchHighlight] = useState<string>(searchTermFromUrl);
  // The conversation the current highlight belongs to (a `${mode}:${id}` key),
  // so we can auto-clear the highlight when the user navigates to a *different*
  // conversation. `pendingHighlightKeyRef` holds the target of an in-flight
  // search-open so the transition to it isn't mistaken for a navigation-away.
  const highlightKeyRef = useRef<string | null>(null);
  const pendingHighlightKeyRef = useRef<string | null>(null);
  const [globalSettingsOpen, setGlobalSettingsOpen] = useState(false);
  // Core categories plus, via `SectionHost.openSettings`, a plugin page's tab id
  // (`plugin:<id>` — see SettingsPanel), which is why this isn't a closed union.
  const [settingsCategory, setSettingsCategory] = useState<string | undefined>(
    undefined,
  );
  // Agent setup and recovery land on the Agents category rather than making
  // the user hunt for the relevant toggle or runtime controls.
  const openAgentSettings = useCallback(() => {
    setSettingsCategory("agents");
    setGlobalSettingsOpen(true);
  }, []);
  // Collections filter the topic tree; the selection is per-browser, not in the URL.
  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeCollectionId, setActiveCollectionId] = useState<number | null>(null);
  const [archiveOpen, setArchiveOpen] = useState(false);
  // Route state owned by the active plugin section (opaque to core).
  const [pluginRoute, setPluginRoute] = useState<PluginRoute>(() => {
    const r = parseAppRoute();
    return { segments: r.pluginSegments, hash: r.pluginHash };
  });
  const [topicSettingsOpen, setTopicSettingsOpen] = useState(false);
  const [topicSettingsTab, setTopicSettingsTab] = useState<"settings" | "context">(
    "settings",
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // Vertical-nav choice, shared with the sidebar. Drives whether the home
  // launcher also shows the standalone rail ("tabs" has no standalone form).
  const [navStyle] = useSidebarNavStyle();
  // Phone-sized viewports can't afford a permanent sidebar column, so the whole
  // navigation surface moves into an off-canvas drawer that overlays the main
  // pane. `narrow` drives every layout branch that differs; `mobileNavOpen` is
  // the drawer's state and is meaningless when the sidebar is in-flow.
  const narrow = useIsNarrow();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const closeMobileNav = useCallback(() => setMobileNavOpen(false), []);
  // Growing past the breakpoint puts the sidebar back in the flow; drop the
  // drawer state so returning to a narrow viewport doesn't reopen it.
  useEffect(() => {
    if (!narrow) setMobileNavOpen(false);
  }, [narrow]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  useGlobalShortcuts({ mobileNavOpen, setMobileNavOpen, setPaletteOpen });
  // The parent topic preselected in the inline "new topic" form (set by the
  // sidebar "+" and the tree's per-node "+ child"). `null` means top level.
  const [topicDraftParentId, setTopicDraftParentId] = useState<number | null>(null);
  // Bumped on every create action so the inline form remounts (and re-focuses
  // its title) even when the preselected parent is unchanged.
  const [topicDraftNonce, setTopicDraftNonce] = useState(0);
  const [chatReloadKey, setChatReloadKey] = useState(0);
  // Fired reminders awaiting acknowledgment, surfaced in the sidebar.
  const [reminders, setReminders] = useState<ReminderItem[]>([]);
  const [sidebarReminder, setSidebarReminder] = useState<{
    container: "topic" | "chat";
    id: number;
  } | null>(null);
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
  const settingsReady = useSettingsReady();

  // Sections contributed by installed backend plugins. `null` until the first
  // fetch resolves, which the gating effect below waits for so a deep link into
  // a plugin section isn't bounced to Topics on the way in. Held in a store so
  // toggling a plugin in Settings updates the sidebar, home launcher, palette
  // and router at once, without reloading the page.
  const pluginDescriptors = usePluginDescriptors();
  const enabledSections = useMemo(
    () => resolveSections(pluginDescriptors),
    [pluginDescriptors],
  );
  const activeSection = useMemo(
    () => enabledSections.find((sec) => sec.id === sidebarMode) ?? null,
    [enabledSections, sidebarMode],
  );

  const issueAssociationsEnabled = settings?.issue_associations_enabled ?? true;
  const globalGithubRepo = (settings?.github_repo ?? "").trim();
  const agentsEnabled = settings?.agents_enabled ?? false;
  const liveEnabled = settings?.live_enabled ?? true;
  const agentsAvailable = settings?.agents_available ?? false;
  const agentsRuntimeStarted = settings?.agents_runtime_started ?? false;
  const agentsUnavailableReason = settings?.agents_unavailable_reason ?? null;

  const issueContext = useIssueContext(activeTopic, setActiveTopic);

  const confirmAction = useConfirm();

  // Mirror activeTopic into a ref so the onComplete callback (set up once)
  // can read the current value without resubscribing on every change.
  const activeTopicRef = useRef<Topic | null>(activeTopic);
  useEffect(() => {
    activeTopicRef.current = activeTopic;
  }, [activeTopic]);

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
    if (mode === "chats" && chatsCtl.activeChatRef.current) {
      return { kind: "chat", id: chatsCtl.activeChatRef.current.id };
    }
    if (mode === "agents" && agentsCtl.activeAgentIdRef.current != null) {
      return { kind: "agent", id: agentsCtl.activeAgentIdRef.current };
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

  // `tree` holds *every* topic, not just the active collection's: it doubles as
  // the app-wide topic lookup (unread totals, notification titles, URL slug
  // paths, the live-session and agent topic links). Collections are a sidebar
  // filter, so scope it at the point of use — see `collectionTree` below.
  async function refreshTree(): Promise<void> {
    setTree(await api.topics.tree());
  }

  async function refreshCollections(): Promise<void> {
    let list: Collection[];
    try {
      list = await api.collections.list();
    } catch {
      return; // transient — keep the previous list
    }
    setCollections(list);
    setActiveCollectionId((current) => {
      const next = pickInitialCollection(list, current ?? readStoredCollectionId());
      return next?.id ?? null;
    });
  }

  function selectCollection(id: number): void {
    writeStoredCollectionId(id);
    setActiveCollectionId(id);
  }

  // Explicit switch from the switcher: the collection is a lens over the tree,
  // so drop a selection that belongs to the collection we just left rather than
  // leaving a topic open beside a sidebar that no longer lists it.
  function chooseCollection(id: number): void {
    if (id === activeCollectionId) return;
    selectCollection(id);
    if (activeTopic && activeTopic.collection_id !== id) setActiveTopic(null);
  }

  async function createCollection(name: string): Promise<void> {
    const created = await api.collections.create({ name });
    await refreshCollections();
    selectCollection(created.id);
  }

  async function moveTopicToCollection(topicId: number, collectionId: number): Promise<void> {
    const updated = await api.topics.update(topicId, { collection_id: collectionId });
    // Keep the open topic in step so the URL picks up the new collection slug.
    if (activeTopicRef.current?.id === topicId) setActiveTopic(updated);
    selectCollection(collectionId);
    await Promise.all([refreshTree(), refreshCollections()]);
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
    void refreshCollections();
    void chatsCtl.refreshChatsUnread();
    void skillsStore.load();
    void rolesStore.load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Collections are a sidebar filter over the app-wide tree. A topic's
  // collection cascades to its whole subtree, so filtering the roots is enough.
  const collectionTree = useMemo(
    () =>
      activeCollectionId == null
        ? tree
        : tree.filter((n) => n.collection_id === activeCollectionId),
    [tree, activeCollectionId],
  );

  // Unread per collection, so the switcher can surface activity you'd otherwise
  // only see after switching into that collection.
  const unreadByCollection = useMemo(() => {
    const map: Record<number, number> = {};
    for (const node of tree) {
      if (node.collection_id == null) continue;
      map[node.collection_id] = (map[node.collection_id] ?? 0) + totalUnread([node]);
    }
    return map;
  }, [tree]);

  // Opening a topic from outside the current collection (deep link, search,
  // command palette) follows it rather than showing an empty tree.
  useEffect(() => {
    const target = activeTopic?.collection_id;
    if (target != null && activeCollectionId != null && target !== activeCollectionId) {
      selectCollection(target);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTopic?.collection_id]);

  // Mirror tree + notification setting into refs so the completion callbacks
  // (registered once) read current values without re-subscribing.
  const treeRef = useRef<TopicNode[]>(tree);
  useEffect(() => {
    treeRef.current = tree;
  }, [tree]);
  // Collections back the URL's first `/topics` segment, so the (once-registered)
  // route resolver needs the current list without re-subscribing.
  const collectionsRef = useRef<Collection[]>(collections);
  useEffect(() => {
    collectionsRef.current = collections;
  }, [collections]);
  const activeCollectionIdRef = useRef<number | null>(activeCollectionId);
  useEffect(() => {
    activeCollectionIdRef.current = activeCollectionId;
  }, [activeCollectionId]);
  // Set while resolving a `/t/<uuid>` permalink so the readable URL replaces it
  // instead of stacking a second history entry for the same topic.
  const permalinkRewriteRef = useRef(false);
  // Set while a topic URL is being resolved. The "nothing selected" effect must
  // not normalise the address bar in the meantime — it would drop the incoming
  // deep link (or permalink) before it has had a chance to land.
  const pendingTopicRouteRef = useRef(false);
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
  // The URL path is the single source of truth for mode + selection (the full
  // scheme, parsers and builders live in lib/routes.ts).
  // `syncFromUrl` runs on mount + back/forward; the effects below push the URL
  // when the active item changes. Equality checks break the feedback loop.

  // Adopt a topic the URL resolved to. A deep link (or a permalink) can point
  // outside the collection the user last had open, so the lens follows the
  // topic rather than rendering an empty tree beside it.
  async function adoptTopicFromUrl(t: Topic): Promise<void> {
    setActiveTopic(t);
    if (t.collection_id != null) selectCollection(t.collection_id);
    try {
      await api.topics.markRead(t.id);
      await refreshTree();
    } catch {
      // non-fatal
    }
  }

  useEffect(() => {
    const syncFromUrl = (): void => {
      agentsCtl.setAgentComposerOpen(false);
      workflowsCtl.setWorkflowEditor(null);
      closeMobileNav();
      // Keep the highlight term in step with the URL for reloads / back-forward.
      // Reset the ownership refs so the highlight adopts whichever conversation
      // the URL resolves to (rather than clearing on that first resolution).
      const urlTerm = searchTermFromUrl();
      setSearchHighlight(urlTerm);
      if (urlTerm) {
        highlightKeyRef.current = null;
        pendingHighlightKeyRef.current = null;
      }
      if (isHomePath()) {
        setAtHome(true);
        wsCtl.closeRoute();
        return;
      }
      setAtHome(false);
      const r = parseAppRoute();
      setSidebarMode(r.mode);
      if (r.mode === "workspaces") {
        wsCtl.syncFromRoute();
        return;
      }
      // Left workspaces — clear its route state so a stale slug/path can't leak.
      wsCtl.closeRoute();
      // A plugin section owns everything under its root; hand it the fresh
      // segments/hash and let it reconcile (initial load + back/forward).
      if (isPluginMode(r.mode)) {
        setPluginRoute({ segments: r.pluginSegments, hash: r.pluginHash });
        return;
      }
      if (r.mode === "live") {
        liveCtl.syncFromRoute(r);
        return;
      }
      if (r.mode === "workflows") {
        workflowsCtl.syncFromRoute(r);
        return;
      }
      if (r.mode === "agents") {
        agentsCtl.syncFromRoute(r);
        return;
      }
      if (r.mode === "topics") {
        // `/t/<uuid>` — the immutable permalink. Resolve it, then let the URL
        // effect below rewrite the address bar to the readable path.
        if (r.topicPublicId) {
          const publicId = r.topicPublicId;
          pendingTopicRouteRef.current = true;
          void (async () => {
            try {
              const t = await api.topics.getByPublicId(publicId);
              permalinkRewriteRef.current = true;
              await adoptTopicFromUrl(t);
            } catch {
              // unknown permalink — leave the user where they are
            } finally {
              pendingTopicRouteRef.current = false;
            }
          })();
          return;
        }
        const segs = r.topicPath;
        if (!segs.length) return;
        const slug = segs[segs.length - 1];
        if (activeTopicRef.current?.slug === slug) return;
        pendingTopicRouteRef.current = true;
        void (async () => {
          try {
            const t = await api.topics.getBySlug(slug);
            await adoptTopicFromUrl(t);
          } catch {
            // Not a topic slug. A lone segment is the bare collection route
            // (`/topics/<collection-slug>`) — switch the lens and show the
            // start hero rather than leaving a stale selection.
            if (segs.length !== 1) return;
            try {
              const list = collectionsRef.current.length
                ? collectionsRef.current
                : await api.collections.list();
              const match = list.find((c) => c.slug === slug);
              if (match) {
                selectCollection(match.id);
                setActiveTopic(null);
              }
            } catch {
              // collections unavailable — leave the user where they are
            }
          } finally {
            pendingTopicRouteRef.current = false;
          }
        })();
        return;
      }
      // chats
      chatsCtl.syncFromRoute(r);
    };
    syncFromUrl();
    window.addEventListener("popstate", syncFromUrl);
    return () => window.removeEventListener("popstate", syncFromUrl);
  }, []);

  // ---- Live meeting sessions -------------------------------------------
  // Called after the mount `syncFromUrl` above, like the controllers below, and
  // it matters most here: on a cold `/live/<slug>` load its URL effect first
  // navigates to `/live`, which would lose the slug before the sync read it.
  // Called ahead of the agents and workflows controllers, which take its
  // `confirmLeaveRecording`.
  const liveCtl = useLiveSessionsController({
    sidebarMode,
    atHome,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
    confirmAction,
  });
  const { activeSessionId, confirmLeaveRecording } = liveCtl;

  // ---- Workspaces -------------------------------------------------------
  // Called after the mount `syncFromUrl` like the other section controllers,
  // and ahead of the agents and workflows controllers, which take its stable
  // `closeRoute`. It has no URL effect, so it can't navigate before the sync.
  const wsCtl = useWorkspacesController({
    sidebarMode,
    setSidebarMode,
    closeMobileNav,
  });

  // ---- Agents -----------------------------------------------------------
  // Called after the mount `syncFromUrl` above so its `/agents` URL effect still
  // runs after it: navigating first would drop `?q=` before the sync reads it.
  // The other sections' URL effects are mode-exclusive, so running ahead of them
  // changes nothing. The rest of the file reaches the section through this.
  const agentsCtl = useAgentsController({
    sidebarMode,
    atHome,
    settingsReady,
    agentsEnabled,
    agentsAvailable,
    agentsRuntimeStarted,
    agentsUnavailableReason,
    notificationsEnabledRef,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeWsRoute: wsCtl.closeRoute,
    closeMobileNav,
    confirmAction,
    confirmLeaveRecording,
  });
  const { activeAgentId, agentsUnread, agentsWaiting } = agentsCtl;

  // ---- Workflows --------------------------------------------------------
  // Called after the mount `syncFromUrl` for the same reason as the agents
  // controller: its `/workflows` URL effect must not navigate before the sync
  // has read the entry URL.
  const workflowsCtl = useWorkflowsController({
    sidebarMode,
    atHome,
    settingsReady,
    agentsEnabled,
    notificationsEnabledRef,
    setSidebarMode,
    setAtHome,
    closeWsRoute: wsCtl.closeRoute,
    closeMobileNav,
    confirmLeaveRecording,
  });

  // ---- Chats ------------------------------------------------------------
  // Called after the mount `syncFromUrl` like the other section controllers, so
  // its `/chats` URL effect can't navigate before the sync reads the entry URL.
  // Called last of them so its event bus subscription runs where App's chat
  // branches used to: after the other sections' handlers, ahead of App's.
  const chatsCtl = useChatsController({
    sidebarMode,
    atHome,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
  });
  const { activeChat, chatsUnread } = chatsCtl;

  // Reflect the total unread count in the tab title (always, independent of the
  // notification permission/setting). Cleared title falls back to the base.
  const topicsUnread = useMemo(() => totalUnread(tree), [tree]);
  const unreadByMode = useMemo(
    () => ({ topics: topicsUnread, chats: chatsUnread, agents: agentsUnread }),
    [topicsUnread, chatsUnread, agentsUnread],
  );
  useEffect(() => {
    const n = topicsUnread + chatsUnread + agentsUnread;
    const bell = agentsWaiting > 0 ? "🔔 " : "";
    document.title = n > 0 ? `${bell}(${n}) ${BASE_TITLE}` : `${bell}${BASE_TITLE}`;
  }, [topicsUnread, chatsUnread, agentsUnread, agentsWaiting]);

  // activeTopic -> /topics/<collection>/<…>/<slug>. pushState for a different
  // item (so back/forward walks topics); replaceState when the same item's
  // readable path merely changes — the collection prefix and ancestor chain
  // are only knowable once the tree and collections have loaded, and they move
  // again whenever the topic is re-parented or changes collection.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "topics" || !activeTopic) return;
    // Wait for both, otherwise a deep link like /topics/work/a/b/c would be
    // rewritten to the bare /topics/c and then back again.
    if (tree.length === 0 || collections.length === 0) return;
    const target = topicUrl(tree, activeTopic, collections);
    if (window.location.pathname === target) return;
    const curSegs = window.location.pathname
      .replace(/\/+$/, "")
      .split("/")
      .filter(Boolean);
    const lastSeg = decodeURIComponent(curSegs[curSegs.length - 1] ?? "");
    if (lastSeg === activeTopic.slug || permalinkRewriteRef.current) {
      permalinkRewriteRef.current = false;
      navigate(target, { replace: true });
    } else {
      navigate(target);
    }
  }, [activeTopic, sidebarMode, tree, collections, atHome]);

  // Topics mode with nothing selected -> /topics/<collection>, so the lens is
  // part of the address and a reload lands back in the same collection. This
  // only *normalises* the URL (replaceState); the surfaces that intend a
  // navigation push their own entry first.
  useEffect(() => {
    if (atHome) return;
    if (sidebarMode !== "topics" || activeTopic) return;
    if (collections.length === 0) return;
    // A deep link still resolving would be overwritten before it lands.
    if (pendingTopicRouteRef.current) return;
    const target = topicsModeUrl(collections, activeCollectionId);
    if (window.location.pathname !== target) navigate(target, { replace: true });
  }, [activeTopic, sidebarMode, collections, activeCollectionId, atHome]);

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
    navigate(window.location.pathname + (qs ? `?${qs}` : ""), { replace: true });
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

  // Section navigation always opens the Agents/Workflows overview. Item links
  // use their own handlers so this policy never discards a deep-link target.
  async function changeMode(next: SidebarMode): Promise<void> {
    // Clicking the active mode's tab while on the home launcher still needs to
    // leave home, so only short-circuit when we're already showing that mode.
    if (next === sidebarMode && !atHome && next !== "agents" && next !== "workflows") return;
    if (!(await confirmLeaveRecording())) return;
    setAtHome(false);
    let target = "/topics";
    if (next === "topics") {
      target = activeTopicRef.current
        ? topicUrl(treeRef.current, activeTopicRef.current, collectionsRef.current)
        : topicsModeUrl(collectionsRef.current, activeCollectionIdRef.current);
    } else if (next === "chats") {
      target = chatsCtl.sectionUrl();
    } else if (next === "live") {
      liveCtl.enterOverview();
      target = "/live";
    } else if (next === "agents") {
      agentsCtl.enterOverview();
      target = "/agents";
    } else if (next === "workflows") {
      workflowsCtl.enterOverview();
      target = "/workflows";
    } else if (isPluginMode(next)) {
      // Re-entering a plugin section restores the sub-route it was left at.
      target = pluginSectionUrl(next, pluginRouteRef.current.segments);
    } else {
      target = "/ws";
    }
    if (window.location.pathname !== target) navigate(target);
    if (next === "workspaces") wsCtl.syncFromRoute();
    else wsCtl.closeRoute();
    setSidebarMode(next);
    // These section buttons explicitly select the overview, like a list item.
    if (next === "agents" || next === "workflows") closeMobileNav();
  }

  // Navigate to the root home launcher.
  async function goHome(): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    closeMobileNav();
    if (window.location.pathname !== "/") navigate("/");
    wsCtl.closeRoute();
    setAtHome(true);
  }

  // The sidebar "+" (mode-aware) leaves the home launcher and drops into a
  // mode's start surface. The home cards themselves stay on `/` and reveal the
  // start surface inline (see the home*FromHome handlers below).
  function startNewFromHome(mode: SidebarMode): void {
    setAtHome(false);
    wsCtl.closeRoute();
    if (mode === "topics") {
      setActiveTopic(null);
      setTopicDraftParentId(null);
      setTopicDraftNonce((n) => n + 1);
      navigate(topicsModeUrl(collectionsRef.current, activeCollectionIdRef.current));
      setSidebarMode("topics");
    } else if (mode === "chats") {
      chatsCtl.startNew();
      navigate("/chats");
      setSidebarMode("chats");
    } else if (mode === "live") {
      liveCtl.startNew();
      navigate("/live");
      setSidebarMode("live");
    } else if (mode === "agents") {
      agentsCtl.startNew();
      navigate("/agents");
      setSidebarMode("agents");
    } else if (mode === "workflows") {
      workflowsCtl.startNew();
      navigate("/workflows");
      setSidebarMode("workflows");
    } else {
      navigate("/ws");
      setSidebarMode("workspaces");
      wsCtl.syncFromRoute();
      wsCtl.startNew();
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
    // A sub-topic inherits its parent's collection, which may differ from the
    // one on screen — follow the topic we just created.
    if (topic.collection_id != null) selectCollection(topic.collection_id);
    await refreshTree();
    setActiveTopic(topic);
  }

  // The live-disabled bounce and the live lazy load. Called here, after the
  // `?q=` mirror, to keep their original place in the effect order.
  useLiveSessionsLateEffects(liveCtl, { sidebarMode, liveEnabled, setSidebarMode });

  // Same guard for plugin sections: a section whose backend package is gone —
  // or whose own `isEnabled` turned false (kanban loses its GitHub repo, say) —
  // can't stay open, and a deep link to it must fall back to Topics. Waits for
  // the descriptors *and* settings so a valid deep link isn't bounced before
  // they resolve.
  useEffect(() => {
    if (settings == null || pluginDescriptors == null) return;
    if (!isPluginMode(sidebarMode)) return;
    if (enabledSections.some((sec) => sec.id === sidebarMode)) return;
    // Any unrecognised root segment parses as a candidate plugin section, so
    // this also catches plain typos — all the more reason to replace rather
    // than push, or Back would bounce off the bad URL forever.
    navigate("/topics", { replace: true });
    setSidebarMode("topics");
  }, [enabledSections, pluginDescriptors, sidebarMode, settings]);

  // Whenever any stream finishes, refresh the tree so unread badges and
  // updated_at timestamps reflect the new server state. If the user happens to
  // be viewing the topic that just finished, also mark it read.
  useEffect(() => {
    streamStore.setOnComplete((key) => {
      const [kind, rawId] = key.split(":");
      const id = Number(rawId);
      void (async () => {
        if (kind === "chat") {
          await chatsCtl.handleStreamComplete(id);
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
  // are filtered out inside the bus. The chats controller handles the chat
  // events, and a chat id wins over a topic or agent id on the shared types.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
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
        if (event.chat_id != null) return;
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
        if (event.chat_id != null) return;
        if (event.topic_id != null) {
          streamStore.setRemoteStreaming(convKey("topic", event.topic_id), true);
        }
      } else if (event.type === "stream.ended") {
        if (event.chat_id != null) return;
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
        //
        // The agents and chats controllers refresh their own sections on an
        // agent or chat id.
        if (event.chat_id == null && event.agent_session_id == null) {
          void refreshTree();
        }
      }
    });
    return () => {
      off();
    };
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
            chatsCtl.setChatListReloadKey((k) => k + 1);
            void chatsCtl.refreshChatsUnread();
          } else if (v.kind === "topic") {
            await api.topics.markRead(v.id);
            await refreshTree();
          } else {
            await api.agents.markRead(v.id);
            await agentsCtl.loadAgents();
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
    closeMobileNav();
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

  async function handleTopicReadState(id: number, unread: boolean): Promise<void> {
    if (unread) await api.topics.markUnread(id);
    else await api.topics.markRead(id);
    await refreshTree();
  }

  async function handleTopicPin(id: number, pinned: boolean): Promise<void> {
    const updated = await api.topics.update(id, { pinned });
    if (activeTopicRef.current?.id === id) setActiveTopic(updated);
    await refreshTree();
  }

  async function handleArchiveTopic(id: number): Promise<void> {
    await api.topics.archive(id);
    if (activeTopicRef.current?.id === id) setActiveTopic(null);
    await refreshTree();
  }

  async function handleOpenTopicNotes(id: number): Promise<void> {
    await handleSelect(id);
    openNotes("topic", id);
  }

  // Open a content-search hit from the command palette. Mirrors the per-section
  // deep-link resolution: leave home, switch mode, and reveal the entity by its
  // stable id (topic/chat/live-session row id, or agent internal id).
  async function openSearchResult(result: SearchResult, query: string): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
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
        await chatsCtl.selectChatById(result.entity_id);
      } else if (result.section === "agents") {
        setSidebarMode("agents");
        agentsCtl.setActiveAgentId(result.entity_id);
      } else if (result.section === "live") {
        setSidebarMode("live");
        await liveCtl.openSearchHit(result.entity_id);
      }
    } catch {
      // Entity may have been deleted since the search — leave the user in the
      // section they landed on rather than surfacing an error.
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
        await chatsCtl.selectChatById(item.chat_id);
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
    } else if (item.container === "chat" && chatsCtl.activeChatRef.current?.id === item.chat_id) {
      chatsCtl.setActiveChatReloadKey((k) => k + 1);
    }
  }

  // The sidebar header's single "New" button adapts to the active mode so the
  // create affordance lives in the same place across Topics / Chats / Files.
  // Chats and agents drop the selection to reveal their "start" landing surface;
  // topics open the create dialog directly.
  function handleNew(): void {
    closeMobileNav();
    // The "+" always lands on a mode's create surface, so leave the home
    // launcher (routing to the current mode's start surface) if we're on it.
    if (atHome) {
      startNewFromHome(sidebarMode);
      return;
    }
    if (sidebarMode === "topics") handleCreate(null);
    else if (sidebarMode === "chats") chatsCtl.startNew();
    else if (sidebarMode === "live") liveCtl.startNew();
    else if (sidebarMode === "agents") agentsCtl.startNew();
    else if (sidebarMode === "workflows") workflowsCtl.startNew();
    // Core owns the button; the section owns what it means. A section with no
    // `onNew` has no "+" either (see `supportsNew` in Sidebar).
    else if (isPluginMode(sidebarMode)) {
      activeSection?.onNew?.(sectionHost);
    } else wsCtl.startNew();
  }

  // ---- Plugin sections ---------------------------------------------------

  // Mirror the section sub-route so changeMode can restore it without
  // re-subscribing to every route change.
  const pluginRouteRef = useRef(pluginRoute);
  useEffect(() => {
    pluginRouteRef.current = pluginRoute;
  }, [pluginRoute]);

  // `changeMode` and `handleSelect` are plain function declarations, so every
  // render makes new ones closing over that render's state. The host below is
  // memoised and would pin whichever pair it was built with — and `changeMode`
  // short-circuits on a stale `sidebarMode`, so a section's "open topic" would
  // silently do nothing. Read them through refs instead.
  const changeModeRef = useRef(changeMode);
  const handleSelectRef = useRef(handleSelect);
  useEffect(() => {
    changeModeRef.current = changeMode;
    handleSelectRef.current = handleSelect;
  });

  // The services a plugin section gets from core. Memoised on the values it
  // closes over so a section's effects don't re-run on unrelated app renders.
  const sectionHost = useMemo<SectionHost>(
    () => ({
      segments: pluginRoute.segments,
      hash: pluginRoute.hash,
      navigate: (segments, hash = "", opts) => {
        // Idempotent: a section re-asserting the URL it already has must not
        // spin the render loop that produced it.
        setPluginRoute((prev) =>
          prev.hash === hash &&
          prev.segments.length === segments.length &&
          prev.segments.every((seg, i) => seg === segments[i])
            ? prev
            : { segments, hash },
        );
        const path =
          pluginSectionUrl(sidebarModeRef.current, segments) +
          window.location.search +
          (hash ? `#${hash}` : "");
        if (window.location.pathname + window.location.search + window.location.hash === path) {
          return;
        }
        navigate(path, { replace: !opts?.push });
      },
      openTopic: (topicId: number) => {
        void changeModeRef.current("topics");
        void handleSelectRef.current(topicId);
      },
      openSettings: (pluginPageId?: string) => {
        setSettingsCategory(pluginPageId ? pluginSettingsTab(pluginPageId) : "plugins");
        setGlobalSettingsOpen(true);
      },
      settings,
    }),
    [pluginRoute, settings],
  );

  // ---- Assistant roles --------------------------------------------------
  // Each composer owns its own role pill; this is the shared persistence path
  // they and the `/role` command funnel through. Selecting the default role
  // persists null (which resolves to default server-side). Live sessions are
  // absent on purpose — LiveView persists its own role from its capture
  // toolbar, the way it already does for the meeting language.
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (sidebarMode === "topics" && activeTopic) {
      const updated = await api.topics.update(activeTopic.id, { role_id: roleId });
      if (activeTopicRef.current?.id === activeTopic.id) setActiveTopic(updated);
    } else if (sidebarMode === "chats" && activeChat) {
      await chatsCtl.setRoleForActive(roleId);
    } else if (sidebarMode === "workspaces" && wsCtl.activeWorkspace) {
      await wsCtl.setRoleForActive(roleId);
    } else if (sidebarMode === "agents" && agentsCtl.activeAgent) {
      const updated = await api.agents.update(agentsCtl.activeAgent.id, { role_id: roleId });
      agentsCtl.setAgents((prev) =>
        prev ? prev.map((a) => (a.id === updated.id ? updated : a)) : prev,
      );
    }
  }

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
    // Push rather than let the "nothing selected" effect replace: Back should
    // return to the topic you were reading, not skip past it.
    const target = topicsModeUrl(collectionsRef.current, activeCollectionIdRef.current);
    if (window.location.pathname !== target) navigate(target);
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

  // A section's sidebar and main pane sit in different subtrees, so its own
  // context provider (when it has one) wraps the whole shell.
  const SectionProvider = activeSection?.Provider;
  const shell = (
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
          agents={agentsCtl.agents ?? []}
          onOpenAgent={(id) => {
            void agentsCtl.openAgent(id);
            setPaletteOpen(false);
          }}
          liveEnabled={liveEnabled}
          pluginSections={enabledSections}
          initialQuery={atHome ? "" : searchHighlight.trim()}
        />
      )}
      {atHome && navStyle === "rail" && !narrow && (
        <SectionRail
          mode={sidebarMode}
          atHome
          onGoHome={goHome}
          onOpenPalette={() => setPaletteOpen(true)}
          onModeChange={changeMode}
          onNew={handleNew}
          unreadByMode={unreadByMode}
          liveEnabled={liveEnabled}
          pluginSections={enabledSections}
          footer={
            <PersonaMenu collapsed onOpenSettings={() => setGlobalSettingsOpen(true)} onOpenArchive={() => setArchiveOpen(true)} />
          }
        />
      )}
      {/* Scrim behind the mobile drawer. Kept mounted so it can cross-fade, and
          click-through disabled while the drawer is closed. */}
      {narrow && (
        <div
          className={`fixed inset-0 bg-black/40 transition-opacity duration-200 ${Z_INDEX.SIDEBAR} ${
            mobileNavOpen ? "opacity-100" : "pointer-events-none opacity-0"
          }`}
          aria-hidden="true"
          onClick={closeMobileNav}
        />
      )}
      {/* On phones the sidebar is an off-canvas drawer sliding over the main
          pane — including at home, where it replaces the standalone section
          rail so there's a single navigation affordance. It sits after the
          scrim so it paints above it at the same stacking tier, and goes inert
          while closed to stay out of the tab order. Wider viewports keep the
          sidebar in the flex flow, hence the transparent `contents` wrapper. */}
      {(narrow || !atHome) && (
      <div
        className={
          narrow
            ? `fixed inset-y-0 left-0 flex bg-bg transition-transform duration-200 ${Z_INDEX.SIDEBAR} ${
                mobileNavOpen ? "translate-x-0" : "-translate-x-full"
              }`
            : "contents"
        }
        inert={narrow && !mobileNavOpen}
      >
      <Sidebar
        tree={collectionTree}
        collections={collections}
        activeCollectionId={activeCollectionId}
        unreadByCollection={unreadByCollection}
        onCollectionChange={chooseCollection}
        onCollectionCreate={createCollection}
        onManageCollections={() => {
          setSettingsCategory("collections");
          setGlobalSettingsOpen(true);
        }}
        onMoveToCollection={moveTopicToCollection}
        activeId={activeTopic?.id ?? null}
        streamingTopicIds={streamingTopicIds}
        narrow={narrow}
        onClose={closeMobileNav}
        collapsed={!narrow && sidebarCollapsed}
        mode={sidebarMode}
        onModeChange={changeMode}
        atHome={atHome}
        onGoHome={goHome}
        onOpenPalette={() => setPaletteOpen(true)}
        chatSlot={
          <ChatsSidebarList
            controller={chatsCtl}
            streamingIds={streamingChatIds}
            reminderChatIds={reminderChatIds}
            onOpenReminder={(chat) => setSidebarReminder({ container: "chat", id: chat.id })}
          />
        }
        workspaceSlot={<WorkspacesSidebarList controller={wsCtl} />}
        liveSlot={<LiveSidebarList controller={liveCtl} />}
        agentSlot={
          <AgentsSidebarList
            controller={agentsCtl}
            onOverview={() => void changeMode("agents")}
          />
        }
        workflowSlot={
          <WorkflowsSidebarSlot
            controller={workflowsCtl}
            onOverview={() => void changeMode("workflows")}
          />
        }
        pluginSlot={
          activeSection ? <activeSection.Sidebar host={sectionHost} /> : null
        }
        onToggleCollapsed={() => setSidebarCollapsed((v) => !v)}
        onSelect={handleSelect}
        onNew={handleNew}
        onCreate={handleCreate}
        onRename={handleRenameTopic}
        onSetRead={handleTopicReadState}
        onTogglePin={handleTopicPin}
        onArchive={handleArchiveTopic}
        onOpenReminder={(id) => setSidebarReminder({ container: "topic", id })}
        onOpenNotes={(id) => void handleOpenTopicNotes(id)}
        liveEnabled={liveEnabled}
        reminders={reminders}
        reminderTopicIds={reminderTopicIds}
        onReminderSelect={handleReminderSelect}
        onReminderDone={handleReminderDone}
        onRefresh={refreshTree}
        onOpenGlobalSettings={() => setGlobalSettingsOpen(true)}
        onOpenArchive={() => setArchiveOpen(true)}
        unreadByMode={unreadByMode}
        pluginSections={enabledSections}
      />
      </div>
      )}

      <main className="flex-1 flex flex-col min-w-0">
        {/* One shared header across every mode: active item title on the left,
            mode-specific actions on the right. */}
        <header className="flex items-center justify-between px-3 md:px-4 h-12 border-b border-border gap-2 md:gap-3">
          {narrow && (
            <button
              type="button"
              className="-ml-1 shrink-0 rounded p-2 hover:bg-surface"
              aria-label="Open navigation"
              onClick={() => setMobileNavOpen(true)}
            >
              <Menu size={18} />
            </button>
          )}
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
                  aria-label="Toggle topic summary"
                  data-tooltip={"Topic summary\nStatus, open actions and key information"}
                  onClick={() => toggleTopicSummary(activeTopic.id)}
                >
                  <FileText size={18} />
                </button>
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
            <ChatsHeader controller={chatsCtl} />
          ) : sidebarMode === "workspaces" ? (
            <WorkspacesHeader controller={wsCtl} />
          ) : sidebarMode === "live" ? (
            <LiveHeader
              controller={liveCtl}
              tree={tree}
              globalGithubRepo={globalGithubRepo}
              onOpenTopic={(tid) => {
                changeMode("topics");
                void handleSelect(tid);
              }}
            />
          ) : activeSection ? (
            <span className="truncate font-medium min-w-0 flex-1">
              {activeSection.Title ? (
                <activeSection.Title host={sectionHost} />
              ) : (
                activeSection.label
              )}
            </span>
          ) : sidebarMode === "workflows" ? (
            <WorkflowsHeader />
          ) : (
            <AgentsHeader
              controller={agentsCtl}
              tree={tree}
              onOverview={() => void changeMode("agents")}
              onOpenTopic={(tid) => {
                changeMode("topics");
                void handleSelect(tid);
              }}
            />
          )}
        </header>

        {!atHome && sidebarMode === "agents" && <AgentRunErrorBanner controller={agentsCtl} />}

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
              showPersona={narrow || navStyle === "tabs"}
              pluginSections={enabledSections}
              onNavigate={changeMode}
              onOpenSettings={() => setGlobalSettingsOpen(true)}
              onOpenArchive={() => setArchiveOpen(true)}
              topicSurface={
                <TopicStartHero
                  tree={tree}
                  collectionId={activeCollectionId}
                  onCreated={handleTopicCreated}
                />
              }
              chatSurface={<ChatsHomeSurface controller={chatsCtl} />}
              liveSurface={
                <LiveHomeSurface controller={liveCtl} tree={tree} collections={collections} />
              }
              agentSurface={
                <AgentsHomeSurface controller={agentsCtl} onOpenSettings={openAgentSettings} />
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
              />
            ) : (
              <TopicStartHero
                key={`topic-create-${topicDraftParentId ?? "root"}-${topicDraftNonce}`}
                tree={tree}
                initialParentId={topicDraftParentId}
                collectionId={activeCollectionId}
                onCreated={handleTopicCreated}
              />
            )
          ) : sidebarMode === "chats" ? (
            <ChatsMain
              controller={chatsCtl}
              onRemindersChanged={loadReminders}
              onSetRole={setRoleForActive}
            />
          ) : sidebarMode === "workspaces" ? (
            <WorkspacesMain controller={wsCtl} onSetRole={setRoleForActive} />
          ) : sidebarMode === "live" ? (
            <LiveMain controller={liveCtl} tree={tree} collections={collections} />
          ) : activeSection ? (
            <activeSection.Main host={sectionHost} />
          ) : sidebarMode === "workflows" ? (
            <WorkflowsMain
              controller={workflowsCtl}
              onOpenSettings={openAgentSettings}
              onOpenAgent={(id) => void agentsCtl.openAgent(id)}
            />
          ) : (
            <AgentsMain
              controller={agentsCtl}
              onOpenWorkflow={(id) => void workflowsCtl.openWorkflow(id)}
              onOpenSettings={openAgentSettings}
              onSetRole={setRoleForActive}
            />
          )}
          </SearchHighlightProvider>
        </div>
      </main>

      {globalSettingsOpen && (
        <SettingsPanel
          initialCategory={settingsCategory}
          onCollectionsChanged={refreshCollections}
          onClose={() => {
            setGlobalSettingsOpen(false);
            setSettingsCategory(undefined);
          }}
        />
      )}
      {sidebarReminder && (
        <ReminderModal
          container={sidebarReminder.container}
          containerId={sidebarReminder.id}
          existing={null}
          onClose={() => setSidebarReminder(null)}
          onSaved={() => {
            setSidebarReminder(null);
            void loadReminders();
          }}
        />
      )}

      <ChatsSettingsModal
        controller={chatsCtl}
        collectionId={activeCollectionId}
        onPromoted={async (topic) => {
          changeMode("topics");
          if (topic.collection_id != null) selectCollection(topic.collection_id);
          setActiveTopic(topic);
          await refreshTree();
        }}
      />

      <AgentSettingsModal
        controller={agentsCtl}
        onOpenWorkflow={(workflowId) => void workflowsCtl.openWorkflow(workflowId)}
      />

      <WorkspacesCreateModal controller={wsCtl} />

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
          onChatRestored={() => chatsCtl.setChatListReloadKey((k) => k + 1)}
          onChatDeleted={(id) => {
            if (activeChat?.id === id) chatsCtl.setActiveChat(null);
            chatsCtl.setChatListReloadKey((k) => k + 1);
          }}
          onAgentRestored={() => void agentsCtl.loadAgents()}
          onAgentDeleted={(id) => {
            if (agentsCtl.activeAgentId === id) agentsCtl.setActiveAgentId(null);
            void agentsCtl.loadAgents();
          }}
          onWorkflowsChanged={() => workflowsCtl.refreshWorkflows()}
          onSessionRestored={() => void liveCtl.loadMeetingSessions()}
          onSessionDeleted={(id) => {
            if (liveCtl.activeSessionId === id) liveCtl.setActiveSessionId(null);
            void liveCtl.loadMeetingSessions();
          }}
        />
      )}

      {topicSettingsOpen && activeTopic && (
        <TopicSettingsPanel
          topic={activeTopic}
          tree={collectionTree}
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

  return SectionProvider ? (
    <SectionProvider host={sectionHost}>{shell}</SectionProvider>
  ) : (
    shell
  );
}
