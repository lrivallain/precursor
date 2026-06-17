import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock,
  FolderGit2,
  MessageSquare,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Pin,
  Plus,
  Search,
  Settings2,
} from "lucide-react";
import type { TopicNode } from "../lib/types";
import { PersonaMenu } from "./PersonaMenu";
import { ResizeHandle } from "./ResizeHandle";
import { useResizableWidth } from "../lib/useResizableWidth";

export type SidebarMode = "topics" | "chats" | "workspaces";

interface Props {
  tree: TopicNode[];
  activeId: number | null;
  streamingTopicIds: number[];
  collapsed: boolean;
  mode: SidebarMode;
  onModeChange: (mode: SidebarMode) => void;
  /** Rendered in the body when mode === "chats" (the chat list). */
  chatSlot?: ReactNode;
  /** Rendered in the body when mode === "workspaces" (the workspace list). */
  workspaceSlot?: ReactNode;
  onToggleCollapsed: () => void;
  onSelect: (id: number) => void;
  /** Mode-aware "New" action (topic / chat / workspace) in the header. */
  onNew: () => void;
  onCreate: (parentId: number | null) => void;
  onCreateSchedule: () => void;
  onEditSchedule: (topicId: number) => void;
  onRefresh: () => Promise<void> | void;
  onOpenGlobalSettings: () => void;
  onOpenArchive: () => void;
}

export function Sidebar({
  tree,
  activeId,
  streamingTopicIds,
  collapsed,
  mode,
  onModeChange,
  chatSlot,
  workspaceSlot,
  onToggleCollapsed,
  onSelect,
  onNew,
  onCreate,
  onCreateSchedule,
  onEditSchedule,
  onOpenGlobalSettings,
  onOpenArchive,
}: Props) {
  const [query, setQuery] = useState("");
  const { collapsedIds, toggleCollapsed } = useCollapsedTopics();
  const { collapsedSections, toggleSection } = useCollapsedSections();
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
  // Scheduled topics get their own section (just after Pinned). They live under
  // the hidden "schedule_root" folder, which we lift out of the main tree so
  // they aren't shown twice.
  const scheduled = useMemo(() => collectScheduled(filtered), [filtered]);
  const mainTree = useMemo(
    () => filtered.filter((n) => n.kind !== "schedule_root"),
    [filtered],
  );

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
        <button
          className={`p-2 rounded ${mode === "topics" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Topics"
          data-tooltip="Topics"
          onClick={() => onModeChange("topics")}
        >
          <MessagesSquare size={18} />
        </button>
        <button
          className={`p-2 rounded ${mode === "chats" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Chats"
          data-tooltip="Chats"
          onClick={() => onModeChange("chats")}
        >
          <MessageSquare size={18} />
        </button>
        <button
          className={`p-2 rounded ${mode === "workspaces" ? "bg-accent/15 text-accent" : "hover:bg-surface"}`}
          aria-label="Workspaces"
          data-tooltip="Workspaces"
          onClick={() => onModeChange("workspaces")}
        >
          <FolderGit2 size={18} />
        </button>
        <div className="my-1 h-px w-6 bg-border" />
        <button
          className="p-2 rounded hover:bg-surface"
          aria-label={
            mode === "topics" ? "New topic" : mode === "chats" ? "New chat" : "New workspace"
          }
          data-tooltip={
            mode === "topics" ? "New topic" : mode === "chats" ? "New chat" : "New workspace"
          }
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
        <img
          src="/logo.svg"
          alt=""
          aria-hidden="true"
          width={22}
          height={22}
          className="rounded-md shrink-0"
        />
        <div className="flex-1 font-semibold tracking-tight">Precursor</div>
        <button
          className="p-1.5 rounded hover:bg-surface"
          aria-label={
            mode === "topics" ? "New topic" : mode === "chats" ? "New chat" : "New workspace"
          }
          data-tooltip={
            mode === "topics" ? "New topic" : mode === "chats" ? "New chat" : "New workspace"
          }
          onClick={onNew}
        >
          <Plus size={16} />
        </button>
        {mode === "topics" && (
          <button
            className="p-1.5 rounded hover:bg-surface"
            aria-label="New scheduled topic"
            data-tooltip="New scheduled topic"
            onClick={onCreateSchedule}
          >
            <Clock size={16} />
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

      {/* Mode switcher: Topics ⟷ Chats ⟷ Workspaces. Persona + settings stay
          visible at the bottom of the sidebar across every mode. */}
      <div className="flex gap-1 px-2 py-2 border-b border-border">
        <button
          className={`flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1.5 text-sm ${
            mode === "topics" ? "bg-accent/15 text-accent" : "hover:bg-surface text-muted"
          }`}
          onClick={() => onModeChange("topics")}
        >
          <MessagesSquare size={14} /> Topics
        </button>
        <button
          className={`flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1.5 text-sm ${
            mode === "chats" ? "bg-accent/15 text-accent" : "hover:bg-surface text-muted"
          }`}
          onClick={() => onModeChange("chats")}
        >
          <MessageSquare size={14} /> Chats
        </button>
        <button
          className={`flex flex-1 items-center justify-center gap-1.5 rounded px-2 py-1.5 text-sm ${
            mode === "workspaces" ? "bg-accent/15 text-accent" : "hover:bg-surface text-muted"
          }`}
          onClick={() => onModeChange("workspaces")}
        >
          <FolderGit2 size={14} /> Files
        </button>
      </div>

      {mode === "chats" ? (
        chatSlot
      ) : mode === "workspaces" ? (
        workspaceSlot
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
                  />
                ))}
              </ul>
            )}
            <div className="mt-2 border-t border-border" />
          </div>
        )}
        {scheduled.length > 0 && (
          <div className="mb-2">
            <SectionHeader
              icon={<Clock size={11} />}
              label="Scheduled"
              collapsed={collapsedSections.has("scheduled")}
              onToggle={() => toggleSection("scheduled")}
            />
            {!collapsedSections.has("scheduled") && (
              <ul className="space-y-0.5">
                {scheduled.map((node) => (
                  <TopicItem
                    key={`scheduled-${node.id}`}
                    node={node}
                    depth={0}
                    activeId={activeId}
                    streamingTopicIds={streamingTopicIds}
                    collapsedIds={collapsedIds}
                    onToggleCollapsed={toggleCollapsed}
                    onSelect={onSelect}
                    onCreate={onCreate}
                    onEditSchedule={onEditSchedule}
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
                onEditSchedule={onEditSchedule}
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
  onEditSchedule: (topicId: number) => void;
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
  onEditSchedule,
}: ItemProps) {
  const open = !collapsedIds.has(node.id);
  const isActive = node.id === activeId;
  const isStreaming = streamingTopicIds.includes(node.id);
  const hasChildren = node.children.length > 0;
  const isScheduled = node.kind === "scheduled";
  const scheduleDisabled = isScheduled && node.schedule?.enabled === false;
  const scheduleError = isScheduled && node.schedule?.status === "error";

  return (
    <li>
      <div
        className={`group flex items-center gap-1 px-1.5 py-1 rounded cursor-pointer text-sm ${
          isActive ? "bg-surface text-text" : "hover:bg-surface text-text/90"
        }`}
        style={{ paddingLeft: 6 + depth * 12 }}
      >
        {isScheduled ? (
          <span
            className={`p-0.5 inline-flex ${
              scheduleDisabled ? "text-muted/50" : "text-muted"
            }`}
            aria-label={scheduleDisabled ? "Schedule paused" : "Scheduled topic"}
            data-tooltip={scheduleDisabled ? "Schedule paused" : "Scheduled topic"}
          >
            <Clock size={13} />
          </span>
        ) : (
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
        )}
        <span
          className={`flex-1 truncate ${
            node.unread_count > 0 && !isStreaming ? "font-semibold" : ""
          } ${scheduleDisabled ? "text-muted" : ""}`}
          onClick={() => onSelect(node.id)}
        >
          {node.title}
        </span>
        {node.pinned && (
          <Pin
            size={11}
            className="text-muted shrink-0"
            aria-label="Pinned"
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
        {isScheduled ? (
          <button
            className="p-0.5 opacity-0 group-hover:opacity-100 text-muted hover:text-text"
            onClick={(e) => {
              e.stopPropagation();
              onEditSchedule(node.id);
            }}
            aria-label="Edit schedule"
            data-tooltip="Edit schedule"
          >
            <Settings2 size={12} />
          </button>
        ) : (
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
        )}
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
              onEditSchedule={onEditSchedule}
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

const COLLAPSED_SECTIONS_KEY = "precursor:sidebar:collapsedSections";

type SectionKey = "pinned" | "scheduled";

// Tracks which named sidebar sections (Pinned / Scheduled) are collapsed and
// persists the collapsed set so the choice survives reloads.
function useCollapsedSections() {
  const [collapsed, setCollapsed] = useState<Set<SectionKey>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(COLLAPSED_SECTIONS_KEY);
      if (!raw) return new Set();
      const keys = JSON.parse(raw) as unknown;
      if (!Array.isArray(keys)) return new Set();
      return new Set(
        keys.filter(
          (k): k is SectionKey => k === "pinned" || k === "scheduled",
        ),
      );
    } catch {
      return new Set();
    }
  });

  const toggleSection = useCallback((key: SectionKey) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      if (typeof window !== "undefined") {
        window.localStorage.setItem(
          COLLAPSED_SECTIONS_KEY,
          JSON.stringify([...next]),
        );
      }
      return next;
    });
  }, []);

  return { collapsedSections: collapsed, toggleSection };
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

function collectScheduled(tree: TopicNode[]): TopicNode[] {
  const out: TopicNode[] = [];
  const walk = (nodes: TopicNode[]): void => {
    for (const node of nodes) {
      if (node.kind === "scheduled") out.push(node);
      if (node.children.length > 0) walk(node.children);
    }
  };
  walk(tree);
  out.sort((a, b) => a.title.localeCompare(b.title));
  return out;
}

function SectionHeader({
  icon,
  label,
  collapsed,
  onToggle,
}: {
  icon: React.ReactNode;
  label: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="group w-full flex items-center gap-1.5 px-2 py-1 text-[11px] uppercase tracking-wide text-muted hover:text-text"
    >
      <span className="text-muted group-hover:text-text">
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
      </span>
      {icon}
      <span>{label}</span>
    </button>
  );
}

interface PinnedItemProps {
  node: TopicNode;
  activeId: number | null;
  streamingTopicIds: number[];
  onSelect: (id: number) => void;
}function PinnedItem({ node, activeId, streamingTopicIds, onSelect }: PinnedItemProps) {
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
        <span
          className={`flex-1 truncate ${
            node.unread_count > 0 && !isStreaming ? "font-semibold" : ""
          }`}
        >
          {node.title}
        </span>
        {isStreaming ? (
          <StreamingDots />
        ) : node.unread_count > 0 ? (
          <UnreadBadge count={node.unread_count} />
        ) : null}
      </div>
    </li>
  );
}
