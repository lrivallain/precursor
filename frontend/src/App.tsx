import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Menu } from "lucide-react";
import { Sidebar, SectionRail, type SidebarMode } from "./components/Sidebar";
import { resolveSections } from "./lib/plugins";
import { usePluginDescriptors } from "./lib/pluginStore";
import type { SectionHost } from "./lib/plugins";
import { CommandPalette } from "./components/CommandPalette";
import { McpAuthBanner } from "./components/McpAuthBanner";
import { SettingsPanel, pluginSettingsTab } from "./components/SettingsPanel";
import { HomePage } from "./components/HomePage";
import { ArchivePanel } from "./components/ArchivePanel";
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
import {
  TopicsHeader,
  TopicsHomeSurface,
  TopicsMain,
  TopicsSettingsModal,
} from "./components/TopicsSectionParts";
import { SearchHighlightBanner } from "./components/SearchHighlightBanner";
import { PersonaMenu } from "./components/PersonaMenu";
import { DetachedDraftHost } from "./components/DetachedDraftHost";
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
import { streamStore, useStreamVersion } from "./lib/streamStore";
import { useGlobalShortcuts } from "./lib/useGlobalShortcuts";
import { useIsNarrow } from "./lib/useMediaQuery";
import { useSidebarNavStyle } from "./lib/useSidebarNavStyle";
import { useAgentsController } from "./lib/useAgentsController";
import { useChatsController } from "./lib/useChatsController";
import {
  useLiveSessionsController,
  useLiveSessionsLateEffects,
} from "./lib/useLiveSessionsController";
import { useReadSync } from "./lib/useReadSync";
import { useSearchHighlightController } from "./lib/useSearchHighlightController";
import { useTopicsController } from "./lib/useTopicsController";
import { useDocumentTitle } from "./lib/useDocumentTitle";
import { pageTitle } from "./lib/pageTitle";
import { useWorkflowsController } from "./lib/useWorkflowsController";
import { useWorkspacesController } from "./lib/useWorkspacesController";
import type { ReminderItem, SearchResult } from "./lib/types";
import {
  isHomePath,
  isPluginMode,
  navigate,
  parseAppRoute,
  pluginSectionUrl,
  type PluginRoute,
} from "./lib/routes";

export default function App() {
  // The active sidebar mode. The URL path owns the mode + selection, so a deep
  // link (or reload onto /topics, /chats, /ws) starts the app in that mode.
  const [sidebarMode, setSidebarMode] = useState<SidebarMode>(() => parseAppRoute().mode);
  // The root path `/` shows the home launcher instead of any mode's content.
  const [atHome, setAtHome] = useState<boolean>(() => isHomePath());
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
  const [archiveOpen, setArchiveOpen] = useState(false);
  // Route state owned by the active plugin section (opaque to core).
  const [pluginRoute, setPluginRoute] = useState<PluginRoute>(() => {
    const r = parseAppRoute();
    return { segments: r.pluginSegments, hash: r.pluginHash };
  });
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

  const confirmAction = useConfirm();

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
    if (mode === "topics" && topicsCtl.activeTopicRef.current) {
      return { kind: "topic", id: topicsCtl.activeTopicRef.current.id };
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

  // The topics loads stay here rather than in the topics controller: this runs
  // before the mount `syncFromUrl`, so the tree and collections requests go out
  // ahead of any topic URL it resolves. That order decides whether a deep link
  // that fails to resolve stays in the address bar, since the controller's
  // "nothing selected" URL effect waits on the collections and on the pending
  // resolution.
  useEffect(() => {
    void topicsCtl.refreshTree();
    void topicsCtl.refreshCollections();
    void chatsCtl.refreshChatsUnread();
    void skillsStore.load();
    void rolesStore.load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const notificationsEnabledRef = useRef(false);
  useEffect(() => {
    notificationsEnabledRef.current = settings?.notifications_enabled ?? false;
  }, [settings]);

  // ---- Path-based routing ----------------------------------------------
  // The URL path is the single source of truth for mode + selection (the full
  // scheme, parsers and builders live in lib/routes.ts).
  // `syncFromUrl` runs on mount + back/forward; the effects below push the URL
  // when the active item changes. Equality checks break the feedback loop.

  useEffect(() => {
    const syncFromUrl = (): void => {
      agentsCtl.setAgentComposerOpen(false);
      workflowsCtl.setWorkflowEditor(null);
      closeMobileNav();
      highlightCtl.syncFromUrl();
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
        topicsCtl.syncFromRoute(r);
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
  // Called after every other section controller but topics, so its event bus
  // subscription runs where App's chat branches used to: after the other
  // sections' handlers, ahead of the topic ones.
  const chatsCtl = useChatsController({
    sidebarMode,
    atHome,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
  });
  const { activeChat, chatsUnread } = chatsCtl;

  // ---- Topics -----------------------------------------------------------
  // Called after the mount `syncFromUrl` like the other section controllers, so
  // its two `/topics` URL effects can't navigate before the sync reads the entry
  // URL. Called after the chats controller so its event bus subscription runs
  // where App's topic branches used to: after the other sections' handlers.
  const topicsCtl = useTopicsController({
    sidebarMode,
    atHome,
    notificationsEnabledRef,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
  });
  const { tree, collections, activeTopic, activeCollectionId } = topicsCtl;

  // ---- Search highlight ---------------------------------------------------
  // Called after every section controller: its `?q=` mirror must run after
  // their pathname URL effects in the same commit, or a navigation drops `q`.
  const highlightCtl = useSearchHighlightController({
    atHome,
    sidebarMode,
    activeTopic,
    activeChat,
    activeAgentId,
    activeSessionId,
  });

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
      target = topicsCtl.sectionUrl();
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
      topicsCtl.startNew();
      navigate(topicsCtl.modeUrl());
      setSidebarMode("topics");
    } else if (mode === "chats") {
      chatsCtl.startNew();
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

  // The stream-completion owner and the mark-read on refocus.
  useReadSync({ currentlyViewed, topics: topicsCtl, chats: chatsCtl, agents: agentsCtl });

  // Live sync across windows: any mutation in another tab/process pushes an
  // event over /api/events. Echoes (events tagged with our own client id)
  // are filtered out inside the bus. The section controllers handle their own
  // events; App keeps the reminders.
  useEffect(() => {
    eventBus.start();
    const off = eventBus.subscribe((event) => {
      if (event.type === "reminder.changed") {
        // A reminder was set, fired, or cleared (possibly by the background
        // ticker). Reload the sidebar section; loadReminders also notifies for
        // any newly-fired ones.
        void loadReminders();
      }
    });
    return () => {
      off();
    };
  }, []);

  // Open a content-search hit from the command palette. Mirrors the per-section
  // deep-link resolution: leave home, switch mode, and reveal the entity by its
  // stable id (topic/chat/live-session row id, or agent internal id).
  async function openSearchResult(result: SearchResult, query: string): Promise<void> {
    if (!(await confirmLeaveRecording())) return;
    setAtHome(false);
    highlightCtl.openForSearch(`${result.section}:${result.entity_id}`, query.trim());
    try {
      if (result.section === "topics") {
        setSidebarMode("topics");
        await topicsCtl.handleSelect(result.entity_id);
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
        await topicsCtl.handleSelect(item.topic_id);
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
    if (item.container === "topic" && topicsCtl.activeTopicRef.current?.id === item.topic_id) {
      topicsCtl.setChatReloadKey((k) => k + 1);
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
    if (sidebarMode === "topics") topicsCtl.handleCreate(null);
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

  // The item a plugin section reports for the tab title, tagged with the
  // section that reported it so no other section ever inherits it.
  const [pluginPageTitle, setPluginPageTitle] = useState<{
    section: string;
    title: string | null;
  } | null>(null);
  // Leaving the section forgets it. This runs after the entered section's own
  // mount effects, hence the tag check rather than an unconditional reset.
  useEffect(() => {
    setPluginPageTitle((prev) => (prev && prev.section !== sidebarMode ? null : prev));
  }, [sidebarMode]);

  // `changeMode` and `handleSelect` are plain function declarations, so every
  // render makes new ones closing over that render's state. The host below is
  // memoised and would pin whichever pair it was built with — and `changeMode`
  // short-circuits on a stale `sidebarMode`, so a section's "open topic" would
  // silently do nothing. Read them through refs instead.
  const changeModeRef = useRef(changeMode);
  const handleSelectRef = useRef(topicsCtl.handleSelect);
  useEffect(() => {
    changeModeRef.current = changeMode;
    handleSelectRef.current = topicsCtl.handleSelect;
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
      // Tagged with this render's mode, not `sidebarModeRef`: a section entering
      // reports from its mount effects, which run before App's ref catches up.
      setPageTitle: (title) => {
        const next = title?.trim() || null;
        setPluginPageTitle((prev) =>
          prev?.section === sidebarMode && prev.title === next
            ? prev
            : { section: sidebarMode, title: next },
        );
      },
      settings,
    }),
    [pluginRoute, settings, sidebarMode],
  );

  // After every hook that navigates from an effect (see useDocumentTitle).
  const unreadByMode = useDocumentTitle({
    page: pageTitle({
      atHome,
      mode: sidebarMode,
      topics: topicsCtl,
      chats: chatsCtl,
      live: liveCtl,
      agents: agentsCtl,
      workflows: workflowsCtl,
      workspaces: wsCtl,
      pluginItem: pluginPageTitle?.section === sidebarMode ? pluginPageTitle.title : null,
    }),
    topicsUnread: topicsCtl.topicsUnread,
    chatsUnread,
    agentsUnread,
    agentsWaiting,
  });

  // ---- Assistant roles --------------------------------------------------
  // Each composer owns its own role pill; this is the shared persistence path
  // they and the `/role` command funnel through. Selecting the default role
  // persists null (which resolves to default server-side). Live sessions are
  // absent on purpose — LiveView persists its own role from its capture
  // toolbar, the way it already does for the meeting language.
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (sidebarMode === "topics" && activeTopic) {
      await topicsCtl.setRoleForActive(roleId);
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
          initialQuery={atHome ? "" : highlightCtl.searchHighlight.trim()}
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
        tree={topicsCtl.collectionTree}
        collections={collections}
        activeCollectionId={activeCollectionId}
        unreadByCollection={topicsCtl.unreadByCollection}
        onCollectionChange={topicsCtl.chooseCollection}
        onCollectionCreate={topicsCtl.createCollection}
        onManageCollections={() => {
          setSettingsCategory("collections");
          setGlobalSettingsOpen(true);
        }}
        onMoveToCollection={topicsCtl.moveTopicToCollection}
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
        onSelect={topicsCtl.handleSelect}
        onNew={handleNew}
        onCreate={topicsCtl.handleCreate}
        onRename={topicsCtl.handleRenameTopic}
        onSetRead={topicsCtl.handleTopicReadState}
        onTogglePin={topicsCtl.handleTopicPin}
        onArchive={topicsCtl.handleArchiveTopic}
        onOpenReminder={(id) => setSidebarReminder({ container: "topic", id })}
        onOpenNotes={(id) => void topicsCtl.handleOpenTopicNotes(id)}
        liveEnabled={liveEnabled}
        reminders={reminders}
        reminderTopicIds={reminderTopicIds}
        onReminderSelect={handleReminderSelect}
        onReminderDone={handleReminderDone}
        onRefresh={topicsCtl.refreshTree}
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
            <TopicsHeader
              controller={topicsCtl}
              issueAssociationsEnabled={issueAssociationsEnabled}
            />
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
                void topicsCtl.handleSelect(tid);
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
                void topicsCtl.handleSelect(tid);
              }}
            />
          )}
        </header>

        {!atHome && sidebarMode === "agents" && <AgentRunErrorBanner controller={agentsCtl} />}

        <McpAuthBanner />

        <SearchHighlightBanner controller={highlightCtl} atHome={atHome} />

        <div className="flex-1 min-h-0">
          <SearchHighlightProvider term={atHome ? "" : highlightCtl.searchHighlight.trim()}>
          {atHome ? (
            <HomePage
              liveEnabled={liveEnabled}
              showPersona={narrow || navStyle === "tabs"}
              pluginSections={enabledSections}
              onNavigate={changeMode}
              onOpenSettings={() => setGlobalSettingsOpen(true)}
              onOpenArchive={() => setArchiveOpen(true)}
              topicSurface={<TopicsHomeSurface controller={topicsCtl} />}
              chatSurface={<ChatsHomeSurface controller={chatsCtl} />}
              liveSurface={
                <LiveHomeSurface controller={liveCtl} tree={tree} collections={collections} />
              }
              agentSurface={
                <AgentsHomeSurface controller={agentsCtl} onOpenSettings={openAgentSettings} />
              }
            />
          ) : sidebarMode === "topics" ? (
            <TopicsMain
              controller={topicsCtl}
              onRemindersChanged={loadReminders}
              onSetRole={setRoleForActive}
            />
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
          onCollectionsChanged={topicsCtl.refreshCollections}
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
          await topicsCtl.openPromotedTopic(topic);
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
            await topicsCtl.refreshTree();
          }}
          onTopicDeleted={async (id) => {
            if (activeTopic?.id === id) topicsCtl.setActiveTopic(null);
            await topicsCtl.refreshTree();
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

      <TopicsSettingsModal controller={topicsCtl} />
    </div>
  );

  return SectionProvider ? (
    <SectionProvider host={sectionHost}>{shell}</SectionProvider>
  ) : (
    shell
  );
}
