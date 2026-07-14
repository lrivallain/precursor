import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  AlarmClock,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  FolderGit2,
  Home,
  MessageSquare,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  Plus,
  Radio,
  Search,
} from "lucide-react";
import type { ReminderItem, TopicNode } from "../lib/types";
import { PersonaMenu } from "./PersonaMenu";
import { ResizeHandle } from "./ResizeHandle";
import { SectionHeader, useCollapsedSections } from "./CollapsibleSection";
import { InlineTitle } from "./InlineTitle";
import { useResizableWidth } from "../lib/useResizableWidth";

export type SidebarMode = "topics" | "chats" | "live" | "workspaces" | "agents";

// Label for the header "New" action, which is mode-aware.
function newActionLabel(mode: SidebarMode): string {
  switch (mode) {
    case "chats":
      return "New chat";
    case "live":
      return "New session";
    case "workspaces":
      return "New workspace";
    case "agents":
      return "New agent";
    default:
      return "New topic";
  }
}

interface Props {
  tree: TopicNode[];
  activeId: number | null;
  streamingTopicIds: number[];
  collapsed: boolean;
  mode: SidebarMode;
  onModeChange: (mode: SidebarMode) => void;
  /** Whether the root home launcher is active (no mode selected). */
  atHome?: boolean;
  /** Navigate to the root home launcher. */
  onGoHome?: () => void;
  /** Rendered in the body when mode === "chats" (the chat list). */
  chatSlot?: ReactNode;
  /** Rendered in the body when mode === "live" (the meeting session list). */
  liveSlot?: ReactNode;
  /** Rendered in the body when mode === "workspaces" (the workspace list). */
  workspaceSlot?: ReactNode;
  /** Rendered in the body when mode === "agents" (the agent session list). */
  agentSlot?: ReactNode;
  onToggleCollapsed: () => void;
  onSelect: (id: number) => void;
  /** Mode-aware "New" action (topic / chat / workspace) in the header. */
  onNew: () => void;
  onCreate: (parentId: number | null) => void;
  /** Inline rename of a topic (double-click its name in the tree). */
  onRename: (id: number, title: string) => void | Promise<void>;
  /** Fired reminders, shown in a dedicated section across topics & chats. */
  reminders: ReminderItem[];
  /** Topic ids with a fired reminder, flagged with an alarm icon in the tree. */
  reminderTopicIds?: Set<number>;
  onReminderSelect: (item: ReminderItem) => void;
  onReminderDone: (item: ReminderItem) => void;
  onRefresh: () => Promise<void> | void;
  onOpenGlobalSettings: () => void;
  onOpenArchive: () => void;
  /** Per-mode unread totals, used to badge the mode switcher tabs. */
  unreadByMode?: Partial<Record<SidebarMode, number>>;
  /** Whether the Live section is enabled (hides its tab when off). */
  liveEnabled?: boolean;
}

export function Sidebar({
  tree,
  activeId,
  streamingTopicIds,
  collapsed,
  mode,
  onModeChange,
  atHome = false,
  onGoHome,
  chatSlot,
  liveSlot,
  workspaceSlot,
  agentSlot,
  onToggleCollapsed,
  onSelect,
  onNew,
  onCreate,
  onRename,
  reminders,
  reminderTopicIds,
  onReminderSelect,
  onReminderDone,
  onOpenGlobalSettings,
  onOpenArchive,
  unreadByMode,
  liveEnabled = true,
}: Props) {
  const [query, setQuery] = useState("");
  const { collapsedIds, toggleCollapsed } = useCollapsedTopics();
  const { collapsed: collapsedSections, toggle: toggleSection } = useCollapsedSections(
    "precursor:sidebar:collapsedSections",
  );
  const { width, onMouseDown: onResizeStart } = useResizableWidth({
    storageKey: "precursor:sidebar:width",
    defaultWidth: 288,
    min: 200,
    max: 520,
  });

  const filtered = useMemo(() => filterTree(tree, query.trim().toLowerCase()), [tree, query]);
  // Pinned topics surface as a flat list at the top of the sidebar so they
  // are always one click away regardless of where they sit in the tree.
  const pinned = useMemo(() => collectPinned(filtered), [filtered]);
  const mainTree = filtered;

  if (collapsed) {
    return (
      <aside className="w-12 border-r border-border flex flex-col items-center py-2 gap-2">
        <img
          src="/logo.svg"
          alt="Precursor"
          width={28}
          height={28}
          className="rounded-md mb-1"
        />
        <button
          className="p-2 rounded hover:bg-surface"
          aria-label="Expand sidebar"
          data-tooltip="Expand sidebar"
          onClick={onToggleCollapsed}
        >
          <PanelLeftOpen size={18} />
        </button>
        <div className="my-1 h-px w-6 bg-border" />
        {onGoHome && (
          <button
            className={`relative p-2 rounded ${atHome ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
            aria-label="Home"
            data-tooltip="Home"
            onClick={onGoHome}
          >
            <Home size={18} />
          </button>
        )}
        <button
          className={`relative p-2 rounded ${!atHome && mode === "topics" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Topics"
          data-tooltip="Topics"
          onClick={() => onModeChange("topics")}
        >
          <MessagesSquare size={18} />
          <ModeUnreadDot count={unreadByMode?.topics ?? 0} />
        </button>
        <button
          className={`relative p-2 rounded ${!atHome && mode === "chats" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Chats"
          data-tooltip="Chats"
          onClick={() => onModeChange("chats")}
        >
          <MessageSquare size={18} />
          <ModeUnreadDot count={unreadByMode?.chats ?? 0} />
        </button>
        {liveEnabled && (
          <button
            className={`relative p-2 rounded ${!atHome && mode === "live" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
            aria-label="Live"
            data-tooltip="Live"
            onClick={() => onModeChange("live")}
          >
            <Radio size={18} />
          </button>
        )}
        <button
          className={`p-2 rounded ${!atHome && mode === "workspaces" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Workspaces"
          data-tooltip="Workspaces"
          onClick={() => onModeChange("workspaces")}
        >
          <FolderGit2 size={18} />
        </button>
        <button
          className={`relative p-2 rounded ${!atHome && mode === "agents" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Agents"
          data-tooltip="Agents"
          onClick={() => onModeChange("agents")}
        >
          <Bot size={18} />
          <ModeUnreadDot count={unreadByMode?.agents ?? 0} />
        </button>
        <div className="my-1 h-px w-6 bg-border" />
        <button
          className="p-2 rounded hover:bg-surface"
          aria-label={newActionLabel(mode)}
          data-tooltip={newActionLabel(mode)}
          onClick={onNew}
        >
          <Plus size={18} />
        </button>
        <div className="flex-1" />
        <PersonaMenu collapsed onOpenSettings={onOpenGlobalSettings} onOpenArchive={onOpenArchive} />
      </aside>
    );
  }

  return (
    <aside
      className="relative border-r border-border flex flex-col shrink-0"
      style={{ width }}
    >
      <ResizeHandle onMouseDown={onResizeStart} />
      <div className="flex items-center gap-2 px-3 h-12 border-b border-border">
        <button
          type="button"
          className={`flex flex-1 items-center gap-2 min-w-0 rounded px-1 py-1 text-left ${
            atHome ? "text-accent" : "hover:bg-surface"
          }`}
          aria-label="Home"
          data-tooltip="Home"
          onClick={onGoHome}
        >
          <img
            src="/logo.svg"
            alt=""
            aria-hidden="true"
            width={22}
            height={22}
            className="rounded-md shrink-0"
          />
          <span className="flex-1 truncate font-semibold tracking-tight">Precursor</span>
        </button>
        <button
          className="p-1.5 rounded hover:bg-surface"
          aria-label={newActionLabel(mode)}
          data-tooltip={newActionLabel(mode)}
          onClick={onNew}
        >
          <Plus size={16} />
        </button>
        <button
          className="p-1.5 rounded hover:bg-surface"
          aria-label="Collapse sidebar"
          data-tooltip="Collapse sidebar"
          onClick={onToggleCollapsed}
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      {/* Mode switcher: Topics ⟷ Chats ⟷ Files. Persona + settings stay
          visible at the bottom of the sidebar across every mode. When the
          sidebar is too narrow, overflow modes collapse into a ">>" menu. */}
      <ModeSwitcher
        mode={mode}
        onModeChange={onModeChange}
        atHome={atHome}
        unreadByMode={unreadByMode}
        liveEnabled={liveEnabled}
      />

      {/* Fired reminders surface here across every mode until acknowledged. */}
      {reminders.length > 0 && (
        <div className="px-2 pt-2 border-b border-border">
          <SectionHeader
            icon={<AlarmClock size={11} />}
            label="Reminders"
            collapsed={collapsedSections.has("reminders")}
            onToggle={() => toggleSection("reminders")}
          />
          {!collapsedSections.has("reminders") && (
            <ul className="space-y-0.5 pb-2">
              {reminders.map((item) => (
                <ReminderRow
                  key={`reminder-${item.id}`}
                  item={item}
                  onSelect={onReminderSelect}
                  onDone={onReminderDone}
                />
              ))}
            </ul>
          )}
        </div>
      )}

      {mode === "chats" ? (
        chatSlot
      ) : mode === "live" ? (
        liveSlot
      ) : mode === "workspaces" ? (
        workspaceSlot
      ) : mode === "agents" ? (
        agentSlot
      ) : (
        <>
          <div className="px-3 py-2 border-b border-border">
            <div className="relative">
              <Search
                size={14}
                className="absolute left-2 top-1/2 -translate-y-1/2 text-muted"
              />
              <input
                type="search"
                placeholder="Search topics..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                className="w-full pl-7 pr-2 py-1.5 text-sm bg-surface border border-border rounded outline-none focus:border-accent"
              />
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-2">
            {pinned.length > 0 && (
              <div className="mb-2">
            <SectionHeader
              icon={<Pin size={11} />}
              label="Pinned"
              collapsed={collapsedSections.has("pinned")}
              onToggle={() => toggleSection("pinned")}
            />
            {!collapsedSections.has("pinned") && (
              <ul className="space-y-0.5">
                {pinned.map((node) => (
                  <PinnedItem
                    key={`pinned-${node.id}`}
                    node={node}
                    activeId={activeId}
                    streamingTopicIds={streamingTopicIds}
                    onSelect={onSelect}
                    onRename={onRename}
                    hasReminder={reminderTopicIds?.has(node.id)}
                  />
                ))}
              </ul>
            )}
            <div className="mt-2 border-t border-border" />
          </div>
        )}
        {mainTree.length === 0 ? (
          <div className="text-sm text-muted px-2 py-4">No topics yet.</div>
        ) : (
          <ul className="space-y-0.5">
            {mainTree.map((node) => (
              <TopicItem
                key={node.id}
                node={node}
                depth={0}
                activeId={activeId}
                streamingTopicIds={streamingTopicIds}
                collapsedIds={collapsedIds}
                onToggleCollapsed={toggleCollapsed}
                onSelect={onSelect}
                onCreate={onCreate}
                onRename={onRename}
                reminderTopicIds={reminderTopicIds}
              />
            ))}
          </ul>
        )}
      </div>
        </>
      )}

      <div className="border-t border-border px-2 py-2">
        <PersonaMenu onOpenSettings={onOpenGlobalSettings} onOpenArchive={onOpenArchive} />
      </div>
    </aside>
  );
}

interface ItemProps {
  node: TopicNode;
  depth: number;
  activeId: number | null;
  streamingTopicIds: number[];
  collapsedIds: ReadonlySet<number>;
  onToggleCollapsed: (id: number) => void;
  onSelect: (id: number) => void;
  onCreate: (parentId: number | null) => void;
  onRename: (id: number, title: string) => void | Promise<void>;
  reminderTopicIds?: Set<number>;
}

function TopicItem({
  node,
  depth,
  activeId,
  streamingTopicIds,
  collapsedIds,
  onToggleCollapsed,
  onSelect,
  onCreate,
  onRename,
  reminderTopicIds,
}: ItemProps) {
  const open = !collapsedIds.has(node.id);
  const isActive = node.id === activeId;
  const isStreaming = streamingTopicIds.includes(node.id);
  const hasChildren = node.children.length > 0;
  const isScheduled = node.schedule != null;
  const scheduleDisabled = isScheduled && node.schedule?.enabled === false;
  const scheduleError = isScheduled && node.schedule?.status === "error";

  return (
    <li>
      <div
        className={`group flex items-center gap-1 px-1.5 py-1 rounded cursor-pointer text-sm ${
          isActive ? "bg-surface text-text" : "hover:bg-surface text-text/90"
        }`}
        style={{ paddingLeft: 6 + depth * 12 }}
        onClick={() => onSelect(node.id)}
      >
        <button
          className="p-0.5 text-muted disabled:opacity-30"
          disabled={!hasChildren}
          onClick={(e) => {
            e.stopPropagation();
            onToggleCollapsed(node.id);
          }}
          aria-label={open ? "Collapse" : "Expand"}
          data-tooltip={hasChildren ? (open ? "Collapse" : "Expand") : undefined}
        >
          {hasChildren ? (
            open ? (
              <ChevronDown size={14} />
            ) : (
              <ChevronRight size={14} />
            )
          ) : (
            <span className="inline-block w-3.5" />
          )}
        </button>
        <InlineTitle
          title={node.title}
          onRename={(t) => onRename(node.id, t)}
          className={`flex-1 truncate ${
            (node.unread_count > 0 || reminderTopicIds?.has(node.id)) && !isStreaming
              ? "font-semibold"
              : ""
          } ${scheduleDisabled ? "text-muted" : ""}`}
        />
        {isScheduled && (
          <Clock
            size={11}
            className={`shrink-0 ${scheduleDisabled ? "text-muted/50" : "text-muted"}`}
            aria-label={scheduleDisabled ? "Schedule paused" : "Scheduled"}
            data-tooltip={scheduleDisabled ? "Schedule paused" : "Runs on a schedule"}
          />
        )}
        {node.pinned && (
          <Pin
            size={11}
            className="text-muted shrink-0"
            aria-label="Pinned"
          />
        )}
        {reminderTopicIds?.has(node.id) && (
          <AlarmClock
            size={12}
            className="text-accent shrink-0"
            aria-label="Reminder waiting"
          />
        )}
        {isStreaming ? (
          <StreamingDots />
        ) : scheduleError ? (
          <span
            className="w-2 h-2 rounded-full bg-red-500 shrink-0"
            aria-label="Last run failed"
            data-tooltip="Last run failed"
          />
        ) : node.unread_count > 0 ? (
          <UnreadBadge count={node.unread_count} />
        ) : null}
        {node.github_issue_number && (
          <span className="text-[10px] text-muted">#{node.github_issue_number}</span>
        )}
        <button
          className="p-0.5 opacity-0 group-hover:opacity-100 text-muted hover:text-text"
          onClick={(e) => {
            e.stopPropagation();
            onCreate(node.id);
          }}
          aria-label="Add child topic"
          data-tooltip="Add child topic"
        >
          <Plus size={12} />
        </button>
      </div>
      {hasChildren && open && (
        <ul className="space-y-0.5">
          {node.children.map((child) => (
            <TopicItem
              key={child.id}
              node={child}
              depth={depth + 1}
              activeId={activeId}
              streamingTopicIds={streamingTopicIds}
              collapsedIds={collapsedIds}
              onToggleCollapsed={onToggleCollapsed}
              onSelect={onSelect}
              onCreate={onCreate}
              onRename={onRename}
              reminderTopicIds={reminderTopicIds}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function StreamingDots() {
  return (
    <span className="inline-flex gap-0.5 items-center px-1" aria-label="Assistant is responding">
      <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce [animation-delay:-0.3s]" />
      <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce [animation-delay:-0.15s]" />
      <span className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" />
    </span>
  );
}

function UnreadBadge({ count }: { count: number }) {
  return (
    <span
      className="inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[10px] font-medium rounded-full bg-blue-500 text-white"
      aria-label={`${count} unread ${count === 1 ? "message" : "messages"}`}
    >
      {count > 9 ? "9+" : count}
    </span>
  );
}

// Small dot overlay for the collapsed sidebar's mode icons — signals "this mode
// has unread items" without room for a count.
function ModeUnreadDot({ count }: { count: number }) {
  if (count <= 0) return null;
  return (
    <span
      className="absolute right-1 top-1 h-2 w-2 rounded-full bg-blue-500 ring-2 ring-bg"
      aria-label={`${count} unread`}
    />
  );
}

const COLLAPSED_TOPICS_KEY = "precursor:sidebar:collapsed";

// Tracks which parent topics are collapsed. We persist the collapsed set (not
// the expanded one) so newly created topics default to expanded.
function useCollapsedTopics() {
  const [collapsedIds, setCollapsedIds] = useState<Set<number>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(COLLAPSED_TOPICS_KEY);
      if (!raw) return new Set();
      const ids = JSON.parse(raw) as unknown;
      if (!Array.isArray(ids)) return new Set();
      return new Set(ids.filter((id): id is number => typeof id === "number"));
    } catch {
      return new Set();
    }
  });

  const toggleCollapsed = useCallback((id: number) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      if (typeof window !== "undefined") {
        window.localStorage.setItem(COLLAPSED_TOPICS_KEY, JSON.stringify([...next]));
      }
      return next;
    });
  }, []);

  return { collapsedIds, toggleCollapsed };
}

function filterTree(tree: TopicNode[], q: string): TopicNode[] {
  if (!q) return tree;
  const out: TopicNode[] = [];
  for (const node of tree) {
    const matched = node.title.toLowerCase().includes(q);
    // When a node matches, keep its full subtree so users can drill into
    // descendants that don't themselves match the query. Otherwise, recurse
    // and keep only branches that contain a match.
    const children = matched ? node.children : filterTree(node.children, q);
    if (matched || children.length > 0) {
      out.push({ ...node, children });
    }
  }
  return out;
}

function collectPinned(tree: TopicNode[]): TopicNode[] {
  const out: TopicNode[] = [];
  const walk = (nodes: TopicNode[]): void => {
    for (const node of nodes) {
      if (node.pinned) out.push(node);
      if (node.children.length > 0) walk(node.children);
    }
  };
  walk(tree);
  out.sort((a, b) => a.title.localeCompare(b.title));
  return out;
}

const MODES: { mode: SidebarMode; label: string; icon: ReactNode }[] = [
  { mode: "topics", label: "Topics", icon: <MessagesSquare size={14} /> },
  { mode: "chats", label: "Chats", icon: <MessageSquare size={14} /> },
  { mode: "live", label: "Live", icon: <Radio size={14} /> },
  { mode: "workspaces", label: "Files", icon: <FolderGit2 size={14} /> },
  { mode: "agents", label: "Agents", icon: <Bot size={14} /> },
];

// Horizontal mode switcher: all modes live in a single scrollable row so the
// active mode is never hidden behind an overflow menu. Left/right chevrons
// appear only when the row overflows, and the active tab is auto-scrolled into
// view whenever it changes, keeping the current activity visible.
function ModeSwitcher({
  mode,
  onModeChange,
  atHome = false,
  unreadByMode,
  liveEnabled = true,
}: {
  mode: SidebarMode;
  onModeChange: (mode: SidebarMode) => void;
  atHome?: boolean;
  unreadByMode?: Partial<Record<SidebarMode, number>>;
  liveEnabled?: boolean;
}) {
  const modes = useMemo(
    () => MODES.filter((m) => m.mode !== "live" || liveEnabled),
    [liveEnabled],
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  // Recompute which arrows are actionable based on the current scroll offset.
  const updateArrows = useCallback((): void => {
    const el = scrollRef.current;
    if (!el) return;
    const max = el.scrollWidth - el.clientWidth;
    setCanLeft(el.scrollLeft > 1);
    setCanRight(el.scrollLeft < max - 1);
  }, []);

  // Scroll the active tab fully into view (with a small margin) whenever it is
  // clipped by either edge. Uses viewport rects so it's correct regardless of
  // the surrounding arrow buttons, and keeps a gap so the tab never tucks under
  // an arrow.
  const scrollActiveIntoView = useCallback((): void => {
    const el = scrollRef.current;
    const btn = activeRef.current;
    if (!el || !btn) return;
    const PAD = 8;
    const cRect = el.getBoundingClientRect();
    const bRect = btn.getBoundingClientRect();
    let delta = 0;
    if (bRect.left < cRect.left + PAD) {
      delta = bRect.left - (cRect.left + PAD);
    } else if (bRect.right > cRect.right - PAD) {
      delta = bRect.right - (cRect.right - PAD);
    }
    if (delta !== 0) el.scrollBy({ left: delta, behavior: "smooth" });
  }, []);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateArrows();
    const ro = new ResizeObserver(() => {
      updateArrows();
      scrollActiveIntoView();
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [updateArrows, scrollActiveIntoView, modes.length]);

  // Keep the selected mode fully visible as it changes — and re-run when the
  // arrows toggle, since their appearance narrows the row and can re-clip the
  // active tab. Always prefer showing the current activity in its entirety.
  useEffect(() => {
    scrollActiveIntoView();
    updateArrows();
  }, [mode, modes.length, canLeft, canRight, scrollActiveIntoView, updateArrows]);

  const scrollBy = useCallback((dir: 1 | -1): void => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(el.clientWidth * 0.6, 96), behavior: "smooth" });
  }, []);

  return (
    <div className="relative flex items-stretch gap-1 px-2 py-2 border-b border-border">
      {canLeft && (
        <button
          type="button"
          className="flex shrink-0 items-center justify-center rounded px-1 text-muted hover:bg-surface hover:text-text"
          aria-label="Scroll modes left"
          data-tooltip="Scroll left"
          onClick={() => scrollBy(-1)}
        >
          <ChevronLeft size={16} />
        </button>
      )}
      <div
        ref={scrollRef}
        className="no-scrollbar flex flex-1 gap-1 overflow-x-auto scroll-smooth"
        onScroll={updateArrows}
      >
        {modes.map((m) => {
          const unread = unreadByMode?.[m.mode] ?? 0;
          const isActive = !atHome && mode === m.mode;
          return (
            <button
              key={m.mode}
              ref={isActive ? activeRef : undefined}
              className={`flex shrink-0 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm ${
                isActive ? "bg-accent/15 text-accent" : "hover:bg-surface text-muted"
              }`}
              onClick={() => onModeChange(m.mode)}
            >
              {m.icon} <span className="whitespace-nowrap">{m.label}</span>
              {unread > 0 && !isActive && <UnreadBadge count={unread} />}
            </button>
          );
        })}
      </div>
      {canRight && (
        <button
          type="button"
          className="flex shrink-0 items-center justify-center rounded px-1 text-muted hover:bg-surface hover:text-text"
          aria-label="Scroll modes right"
          data-tooltip="Scroll right"
          onClick={() => scrollBy(1)}
        >
          <ChevronRight size={16} />
        </button>
      )}
    </div>
  );
}

interface ReminderRowProps {
  item: ReminderItem;
  onSelect: (item: ReminderItem) => void;
  onDone: (item: ReminderItem) => void;
}
function ReminderRow({ item, onSelect, onDone }: ReminderRowProps) {
  const Icon = item.container === "chat" ? MessageSquare : MessagesSquare;
  return (
    <li>
      <div
        className="group flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer text-sm hover:bg-surface text-text/90"
        onClick={() => onSelect(item)}
        title={item.note?.trim() || "Reminder"}
      >
        <Icon size={12} className="text-accent shrink-0" />
        <span className="flex-1 truncate font-semibold">{item.title}</span>
        <button
          type="button"
          className="shrink-0 p-0.5 rounded text-muted hover:text-text hover:bg-border opacity-0 group-hover:opacity-100"
          aria-label="Mark reminder done"
          title="Done"
          onClick={(e) => {
            e.stopPropagation();
            onDone(item);
          }}
        >
          <Check size={13} />
        </button>
      </div>
    </li>
  );
}

interface PinnedItemProps {
  node: TopicNode;
  activeId: number | null;
  streamingTopicIds: number[];
  onSelect: (id: number) => void;
  onRename: (id: number, title: string) => void | Promise<void>;
  hasReminder?: boolean;
}
function PinnedItem({
  node,
  activeId,
  streamingTopicIds,
  onSelect,
  onRename,
  hasReminder,
}: PinnedItemProps) {
  const isActive = node.id === activeId;
  const isStreaming = streamingTopicIds.includes(node.id);
  return (
    <li>
      <div
        className={`flex items-center gap-1.5 px-2 py-1 rounded cursor-pointer text-sm ${
          isActive ? "bg-surface text-text" : "hover:bg-surface text-text/90"
        }`}
        onClick={() => onSelect(node.id)}
      >
        <Pin size={12} className="text-muted shrink-0" />
        <InlineTitle
          title={node.title}
          onRename={(t) => onRename(node.id, t)}
          className={`flex-1 truncate ${
            (node.unread_count > 0 || hasReminder) && !isStreaming ? "font-semibold" : ""
          }`}
        />
        {hasReminder && (
          <AlarmClock size={12} className="text-accent shrink-0" aria-label="Reminder waiting" />
        )}
        {isStreaming ? (
          <StreamingDots />
        ) : node.unread_count > 0 ? (
          <UnreadBadge count={node.unread_count} />
        ) : null}
      </div>
    </li>
  );
}
