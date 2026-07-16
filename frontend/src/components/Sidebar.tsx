import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, ReactNode } from "react";
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
  PanelLeft,
  PanelLeftClose,
  PanelLeftOpen,
  PanelTop,
  Pin,
  Plus,
  Radio,
  Search,
  SquareKanban,
} from "lucide-react";
import type { ReminderItem, TopicNode } from "../lib/types";
import { SECTION_COLORS } from "../lib/sections";
import { Z_INDEX } from "../lib/constants";
import { PersonaMenu } from "./PersonaMenu";
import { ResizeHandle } from "./ResizeHandle";
import { SectionHeader, useCollapsedSections } from "./CollapsibleSection";
import { InlineTitle } from "./InlineTitle";
import { useResizableWidth } from "../lib/useResizableWidth";

export type SidebarMode = "topics" | "chats" | "live" | "workspaces" | "agents" | "kanban";

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
  /** Rendered in the body when mode === "kanban" (the project picker list). */
  kanbanSlot?: ReactNode;
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
  /** Opens the command palette (⌘K) for width-independent section jumps. */
  onOpenPalette?: () => void;
  /** Per-mode unread totals, used to badge the mode switcher tabs. */
  unreadByMode?: Partial<Record<SidebarMode, number>>;
  /** Whether the Live section is enabled (hides its tab when off). */
  liveEnabled?: boolean;
  /** Whether the Kanban section is enabled (shown only when a GitHub repo +
      issue associations are configured). */
  kanbanEnabled?: boolean;
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
  kanbanSlot,
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
  onOpenPalette,
  unreadByMode,
  liveEnabled = true,
  kanbanEnabled = false,
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
  // Expanded-sidebar section navigation style. "rail" shows an always-visible
  // vertical icon rail (no section is hidden by width); "tabs" keeps the
  // horizontal, scrollable switcher. Persisted so the choice sticks. Only
  // affects the expanded layout — the collapsed sidebar is always a rail.
  const [navStyle, setNavStyle] = useState<"rail" | "tabs">(() => {
    if (typeof window === "undefined") return "rail";
    return window.localStorage.getItem("precursor:sidebar:navStyle") === "tabs"
      ? "tabs"
      : "rail";
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("precursor:sidebar:navStyle", navStyle);
  }, [navStyle]);

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
        {onOpenPalette && (
          <button
            className="p-2 rounded hover:bg-surface"
            aria-label="Jump to section"
            data-tooltip="Jump to section (⌘K)"
            onClick={onOpenPalette}
          >
            <Search size={18} />
          </button>
        )}
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
        <SectionRailButtons
          mode={mode}
          atHome={atHome}
          onModeChange={onModeChange}
          onNew={onNew}
          unreadByMode={unreadByMode}
          liveEnabled={liveEnabled}
          kanbanEnabled={kanbanEnabled}
        />
        <div className="flex-1" />
        <PersonaMenu collapsed onOpenSettings={onOpenGlobalSettings} onOpenArchive={onOpenArchive} />
      </aside>
    );
  }

  return (
    <div className="flex h-full shrink-0">
      {navStyle === "rail" && (
        <nav className="flex w-12 shrink-0 flex-col items-center gap-1 border-r border-border py-2">
          {onOpenPalette && (
            <>
              <button
                className="p-2 rounded hover:bg-surface"
                aria-label="Jump to section"
                data-tooltip="Jump to section (⌘K)"
                onClick={onOpenPalette}
              >
                <Search size={18} />
              </button>
              <div className="my-1 h-px w-6 bg-border" />
            </>
          )}
          <SectionRailButtons
            mode={mode}
            atHome={atHome}
            onModeChange={onModeChange}
            onNew={onNew}
            unreadByMode={unreadByMode}
            liveEnabled={liveEnabled}
            kanbanEnabled={kanbanEnabled}
            showNew={false}
            labelOnHover
          />
        </nav>
      )}
      <aside
        className="relative border-r border-border flex flex-col shrink-0 min-w-0"
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
          aria-label={navStyle === "rail" ? "Use tab navigation" : "Use rail navigation"}
          data-tooltip={navStyle === "rail" ? "Switch to tabs" : "Switch to rail"}
          onClick={() => setNavStyle((s) => (s === "rail" ? "tabs" : "rail"))}
        >
          {navStyle === "rail" ? <PanelTop size={16} /> : <PanelLeft size={16} />}
        </button>
        {mode !== "kanban" && (
          <button
            className={`p-1.5 rounded-full transition-opacity hover:opacity-80 ${SECTION_COLORS[mode].icon}`}
            aria-label={newActionLabel(mode)}
            data-tooltip={newActionLabel(mode)}
            onClick={onNew}
          >
            <Plus size={16} />
          </button>
        )}
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
          sidebar is too narrow, overflow modes collapse into a ">>" menu.
          Hidden in "rail" mode, where the vertical rail handles switching. */}
      {navStyle === "tabs" && (
        <ModeSwitcher
          mode={mode}
          onModeChange={onModeChange}
          atHome={atHome}
          unreadByMode={unreadByMode}
          liveEnabled={liveEnabled}
          kanbanEnabled={kanbanEnabled}
          onOpenPalette={onOpenPalette}
        />
      )}

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
      ) : mode === "kanban" ? (
        kanbanSlot
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
    </div>
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
          isActive ? "section-selected" : "hover:bg-surface text-text/90"
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

const MODES: {
  mode: SidebarMode;
  label: string;
  Icon: ComponentType<{ size?: number; className?: string }>;
}[] = [
  { mode: "topics", label: "Topics", Icon: MessagesSquare },
  { mode: "chats", label: "Chats", Icon: MessageSquare },
  { mode: "live", label: "Live", Icon: Radio },
  { mode: "workspaces", label: "Files", Icon: FolderGit2 },
  { mode: "agents", label: "Agents", Icon: Bot },
  { mode: "kanban", label: "Kanban", Icon: SquareKanban },
];

// Vertical section rail: an always-visible column of section icons (Home +
// every enabled mode, and — in the collapsed sidebar — the "New" action).
// Shared by the collapsed sidebar and the expanded "rail" navigation style so
// both stay in lockstep. Home is only rendered when `onGoHome` is passed — the
// expanded layout already exposes Home via its "Precursor" header button, so it
// omits it. `showNew` controls the trailing "New" button (kept in the collapsed
// rail, which has no header; the expanded rail relies on the header "+").
// `labelOnHover` reveals the full section name as a flyout pill next to each
// icon (expanded rail) instead of the collapsed rail's hover tooltip.
function SectionRailButtons({
  mode,
  atHome = false,
  onGoHome,
  onModeChange,
  onNew,
  unreadByMode,
  liveEnabled = true,
  kanbanEnabled = false,
  showNew = true,
  labelOnHover = false,
}: {
  mode: SidebarMode;
  atHome?: boolean;
  onGoHome?: () => void;
  onModeChange: (mode: SidebarMode) => void;
  onNew: () => void;
  unreadByMode?: Partial<Record<SidebarMode, number>>;
  liveEnabled?: boolean;
  kanbanEnabled?: boolean;
  showNew?: boolean;
  labelOnHover?: boolean;
}) {
  const modes = MODES.filter(
    (m) => (m.mode !== "live" || liveEnabled) && (m.mode !== "kanban" || kanbanEnabled),
  );
  // Hover label rendered as a flush continuation of the icon: same section
  // tint, no gap/border, squared seam — so it reads as the icon extending into
  // a pill rather than a detached bubble. An opaque base (bg-bg) under the tint
  // keeps content from bleeding through, matching the button's own tint-over-bg.
  const flyout = (label: string, tint: string) =>
    labelOnHover ? (
      <span
        className={`pointer-events-none absolute inset-y-0 left-full flex items-center rounded-r-md bg-bg opacity-0 transition-opacity group-hover:opacity-100 ${Z_INDEX.POPOVER}`}
      >
        <span
          className={`flex h-full items-center whitespace-nowrap rounded-r-md pl-2 pr-3 text-sm font-medium ${tint}`}
        >
          {label}
        </span>
      </span>
    ) : null;
  return (
    <>
      {onGoHome && (
        <button
          className={`group relative p-2 rounded ${atHome ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Home"
          data-tooltip={labelOnHover ? undefined : "Home"}
          onClick={onGoHome}
        >
          <Home size={18} />
          {flyout("Home", "bg-surface text-text")}
        </button>
      )}
      {modes.map((m) => {
        const isActive = !atHome && mode === m.mode;
        return (
          <button
            key={m.mode}
            className={`group relative p-2 rounded ${labelOnHover ? "hover:rounded-r-none" : ""} ${isActive ? SECTION_COLORS[m.mode].activeTab : SECTION_COLORS[m.mode].hoverTab}`}
            aria-label={m.label}
            data-tooltip={labelOnHover ? undefined : m.label}
            onClick={() => onModeChange(m.mode)}
          >
            <m.Icon size={18} />
            <ModeUnreadDot count={unreadByMode?.[m.mode] ?? 0} />
            {flyout(m.label, SECTION_COLORS[m.mode].icon)}
          </button>
        );
      })}
      {showNew && mode !== "kanban" && (
        <>
          <div className="my-1 h-px w-6 bg-border" />
          <button
            className="group relative p-2 rounded hover:bg-surface"
            aria-label={newActionLabel(mode)}
            data-tooltip={labelOnHover ? undefined : newActionLabel(mode)}
            onClick={onNew}
          >
            <Plus size={18} />
            {flyout(newActionLabel(mode), "bg-surface text-text")}
          </button>
        </>
      )}
    </>
  );
}

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
  kanbanEnabled = false,
  onOpenPalette,
}: {
  mode: SidebarMode;
  onModeChange: (mode: SidebarMode) => void;
  atHome?: boolean;
  unreadByMode?: Partial<Record<SidebarMode, number>>;
  liveEnabled?: boolean;
  kanbanEnabled?: boolean;
  onOpenPalette?: () => void;
}) {
  const modes = useMemo(
    () =>
      MODES.filter(
        (m) =>
          (m.mode !== "live" || liveEnabled) && (m.mode !== "kanban" || kanbanEnabled),
      ),
    [liveEnabled, kanbanEnabled],
  );
  const wrapperRef = useRef<HTMLDivElement>(null);
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

  // Recenter the active tab on genuine layout changes (window / sidebar
  // resize), observing the OUTER wrapper rather than the inner scroll row.
  // The arrow buttons are flex siblings of the scroll row, so their appearing
  // or disappearing mid-scroll would resize the row and — if observed — snap
  // the view back to the active tab, making other tabs unreachable via the
  // arrows. The wrapper's width is unaffected by that arrow toggle, so it only
  // fires on real resizes.
  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) return;
    updateArrows();
    const ro = new ResizeObserver(() => {
      updateArrows();
      scrollActiveIntoView();
    });
    ro.observe(wrapper);
    return () => ro.disconnect();
  }, [updateArrows, scrollActiveIntoView, modes.length]);

  // Keep the selected mode fully visible when it *changes*. Deliberately does
  // not depend on canLeft/canRight: those toggle on every manual scroll (via
  // onScroll -> updateArrows), and re-running scrollActiveIntoView here would
  // snap the row back to the active tab, making other tabs unreachable.
  useEffect(() => {
    scrollActiveIntoView();
    updateArrows();
  }, [mode, modes.length, scrollActiveIntoView, updateArrows]);

  const scrollBy = useCallback((dir: 1 | -1): void => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: dir * Math.max(el.clientWidth * 0.6, 96), behavior: "smooth" });
  }, []);

  // Let a vertical mouse wheel scroll the horizontal row: a plain wheel has no
  // deltaX, so without this the row is only reachable via the arrows or a
  // trackpad. Registered natively with { passive: false } because React's
  // synthetic onWheel is passive and can't preventDefault. Only hijacks the
  // wheel when the row actually overflows and the gesture is vertical-dominant,
  // so trackpad horizontal swipes and non-overflowing rows behave normally.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent): void => {
      if (el.scrollWidth <= el.clientWidth) return;
      if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      el.scrollLeft += e.deltaY;
      e.preventDefault();
      updateArrows();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [updateArrows]);

  return (
    <div
      ref={wrapperRef}
      className="relative flex items-stretch gap-1 px-2 py-2 border-b border-border"
    >
      {onOpenPalette && (
        <button
          type="button"
          className="flex shrink-0 items-center justify-center rounded px-1.5 text-muted hover:bg-surface hover:text-text"
          aria-label="Jump to section"
          data-tooltip="Jump to section (⌘K)"
          onClick={onOpenPalette}
        >
          <Search size={16} />
        </button>
      )}
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
                isActive
                  ? SECTION_COLORS[m.mode].activeTab
                  : `text-muted ${SECTION_COLORS[m.mode].hoverTab}`
              }`}
              onClick={() => onModeChange(m.mode)}
            >
              <m.Icon size={14} /> <span className="whitespace-nowrap">{m.label}</span>
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
          isActive ? "section-selected" : "hover:bg-surface text-text/90"
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
