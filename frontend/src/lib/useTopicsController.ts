import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, RefObject, SetStateAction } from "react";
import type { SidebarMode } from "../components/Sidebar";
import { api } from "./api";
import {
  pickInitialCollection,
  readStoredCollectionId,
  writeStoredCollectionId,
} from "./collections";
import { eventBus } from "./events";
import { notifyIfUnfocused } from "./notifications";
import { openNotes } from "./notesOpen";
import { navigate, topicsModeUrl, topicUrl, type AppRoute } from "./routes";
import { convKey, streamStore } from "./streamStore";
import { findTitle, totalUnread } from "./topicTree";
import type { Collection, Topic, TopicNode } from "./types";
import { useIssueContext, type IssueContextState } from "./useIssueContext";
import { windowFocused } from "./windowFocus";

// Shell state the topics section reads or drives. The once-registered listeners
// (App's `syncFromUrl`, the stream completion and focus handler in
// `useReadSync`, the event bus below) keep the first render's controller, so
// everything they call only touches refs, state setters and the stable deps.
export interface TopicsControllerDeps {
  sidebarMode: SidebarMode;
  atHome: boolean;
  notificationsEnabledRef: RefObject<boolean>;
  isViewing: (kind: "topic" | "chat" | "agent", id: number) => boolean;
  setSidebarMode: Dispatch<SetStateAction<SidebarMode>>;
  setAtHome: Dispatch<SetStateAction<boolean>>;
  closeMobileNav: () => void;
}

export interface TopicsController {
  tree: TopicNode[];
  activeTopic: Topic | null;
  setActiveTopic: Dispatch<SetStateAction<Topic | null>>;
  activeTopicRef: RefObject<Topic | null>;
  collections: Collection[];
  activeCollectionId: number | null;
  topicDraftParentId: number | null;
  topicDraftNonce: number;
  chatReloadKey: number;
  setChatReloadKey: Dispatch<SetStateAction<number>>;
  topicSettingsOpen: boolean;
  setTopicSettingsOpen: Dispatch<SetStateAction<boolean>>;
  topicSettingsTab: "settings" | "context";
  issueContext: IssueContextState;
  collectionTree: TopicNode[];
  unreadByCollection: Record<number, number>;
  topicsUnread: number;
  refreshTree: () => Promise<void>;
  refreshCollections: () => Promise<void>;
  selectCollection: (id: number) => void;
  chooseCollection: (id: number) => void;
  createCollection: (name: string) => Promise<void>;
  moveTopicToCollection: (topicId: number, collectionId: number) => Promise<void>;
  handleSelect: (id: number) => Promise<void>;
  handleRenameTopic: (id: number, title: string) => Promise<void>;
  handleTopicReadState: (id: number, unread: boolean) => Promise<void>;
  handleTopicPin: (id: number, pinned: boolean) => Promise<void>;
  handleArchiveTopic: (id: number) => Promise<void>;
  handleOpenTopicNotes: (id: number) => Promise<void>;
  handleCreate: (parentId: number | null) => void;
  handleTopicCreated: (topic: Topic) => Promise<void>;
  openTopicSettings: (tab?: "settings" | "context") => void;
  togglePin: () => Promise<void>;
  openPromotedTopic: (topic: Topic) => Promise<void>;
  handleStreamComplete: (topicId: number) => Promise<void>;
  setRoleForActive: (roleId: number | null) => Promise<void>;
  sectionUrl: () => string;
  modeUrl: () => string;
  syncFromRoute: (r: AppRoute) => void;
  startNew: () => void;
}

export function useTopicsController(deps: TopicsControllerDeps): TopicsController {
  const {
    sidebarMode,
    atHome,
    notificationsEnabledRef,
    isViewing,
    setSidebarMode,
    setAtHome,
    closeMobileNav,
  } = deps;

  const [tree, setTree] = useState<TopicNode[]>([]);
  const [activeTopic, setActiveTopic] = useState<Topic | null>(null);
  // Collections filter the topic tree; the selection is per-browser, not in the URL.
  const [collections, setCollections] = useState<Collection[]>([]);
  const [activeCollectionId, setActiveCollectionId] = useState<number | null>(null);
  const [topicSettingsOpen, setTopicSettingsOpen] = useState(false);
  const [topicSettingsTab, setTopicSettingsTab] = useState<"settings" | "context">(
    "settings",
  );
  // The parent topic preselected in the inline "new topic" form (set by the
  // sidebar "+" and the tree's per-node "+ child"). `null` means top level.
  const [topicDraftParentId, setTopicDraftParentId] = useState<number | null>(null);
  // Bumped on every create action so the inline form remounts (and re-focuses
  // its title) even when the preselected parent is unchanged.
  const [topicDraftNonce, setTopicDraftNonce] = useState(0);
  const [chatReloadKey, setChatReloadKey] = useState(0);

  const issueContext = useIssueContext(activeTopic, setActiveTopic);

  // Mirror activeTopic into a ref so the onComplete callback (set up once)
  // can read the current value without resubscribing on every change.
  const activeTopicRef = useRef<Topic | null>(activeTopic);
  useEffect(() => {
    activeTopicRef.current = activeTopic;
  }, [activeTopic]);

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

  // The topics branch of App's mount + back/forward URL sync.
  function syncFromRoute(r: AppRoute): void {
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
  }

  const topicsUnread = useMemo(() => totalUnread(tree), [tree]);

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

  // The topic branch of App's stream completion: keep the topic read if the
  // user is watching it, refresh the tree, then notify.
  async function handleStreamComplete(id: number): Promise<void> {
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
  }

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

  // The topics branch of App's shared role persistence (`setRoleForActive`).
  async function setRoleForActive(roleId: number | null): Promise<void> {
    if (!activeTopic) return;
    const updated = await api.topics.update(activeTopic.id, { role_id: roleId });
    if (activeTopicRef.current?.id === activeTopic.id) setActiveTopic(updated);
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

  // A chat was promoted to this topic: follow it into its collection, select
  // it, and refresh the tree so it appears.
  async function openPromotedTopic(topic: Topic): Promise<void> {
    if (topic.collection_id != null) selectCollection(topic.collection_id);
    setActiveTopic(topic);
    await refreshTree();
  }

  // Where section navigation lands: the open topic, else the collection root.
  function sectionUrl(): string {
    return activeTopicRef.current
      ? topicUrl(treeRef.current, activeTopicRef.current, collectionsRef.current)
      : topicsModeUrl(collectionsRef.current, activeCollectionIdRef.current);
  }

  // The collection root: the topics start surface's URL.
  function modeUrl(): string {
    return topicsModeUrl(collectionsRef.current, activeCollectionIdRef.current);
  }

  // Drop the selection and reset the draft to reveal a fresh top-level create
  // form.
  function startNew(): void {
    setActiveTopic(null);
    setTopicDraftParentId(null);
    setTopicDraftNonce((n) => n + 1);
  }

  return {
    tree,
    activeTopic,
    setActiveTopic,
    activeTopicRef,
    collections,
    activeCollectionId,
    topicDraftParentId,
    topicDraftNonce,
    chatReloadKey,
    setChatReloadKey,
    topicSettingsOpen,
    setTopicSettingsOpen,
    topicSettingsTab,
    issueContext,
    collectionTree,
    unreadByCollection,
    topicsUnread,
    refreshTree,
    refreshCollections,
    selectCollection,
    chooseCollection,
    createCollection,
    moveTopicToCollection,
    handleSelect,
    handleRenameTopic,
    handleTopicReadState,
    handleTopicPin,
    handleArchiveTopic,
    handleOpenTopicNotes,
    handleCreate,
    handleTopicCreated,
    openTopicSettings,
    togglePin,
    openPromotedTopic,
    handleStreamComplete,
    setRoleForActive,
    sectionUrl,
    modeUrl,
    syncFromRoute,
    startNew,
  };
}
