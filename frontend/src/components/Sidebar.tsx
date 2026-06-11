import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
} from "lucide-react";
import type { TopicNode } from "../lib/types";

interface Props {
  tree: TopicNode[];
  activeId: number | null;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (id: number) => void;
  onCreate: (parentId: number | null) => void;
  onRefresh: () => Promise<void> | void;
}

export function Sidebar({
  tree,
  activeId,
  collapsed,
  onToggleCollapsed,
  onSelect,
  onCreate,
}: Props) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => filterTree(tree, query.trim().toLowerCase()), [tree, query]);

  if (collapsed) {
    return (
      <aside className="w-12 border-r border-border flex flex-col items-center py-2 gap-2">
        <button
          className="p-2 rounded hover:bg-surface"
          aria-label="Expand sidebar"
          onClick={onToggleCollapsed}
        >
          <PanelLeftOpen size={18} />
        </button>
        <button
          className="p-2 rounded hover:bg-surface"
          aria-label="New topic"
          onClick={() => onCreate(null)}
        >
          <Plus size={18} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="w-72 border-r border-border flex flex-col">
      <div className="flex items-center gap-2 px-3 h-12 border-b border-border">
        <div className="flex-1 font-semibold tracking-tight">Precursor</div>
        <button
          className="p-1.5 rounded hover:bg-surface"
          aria-label="New topic"
          onClick={() => onCreate(null)}
        >
          <Plus size={16} />
        </button>
        <button
          className="p-1.5 rounded hover:bg-surface"
          aria-label="Collapse sidebar"
          onClick={onToggleCollapsed}
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

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
        {filtered.length === 0 ? (
          <div className="text-sm text-muted px-2 py-4">No topics yet.</div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((node) => (
              <TopicItem
                key={node.id}
                node={node}
                depth={0}
                activeId={activeId}
                onSelect={onSelect}
                onCreate={onCreate}
              />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

interface ItemProps {
  node: TopicNode;
  depth: number;
  activeId: number | null;
  onSelect: (id: number) => void;
  onCreate: (parentId: number | null) => void;
}

function TopicItem({ node, depth, activeId, onSelect, onCreate }: ItemProps) {
  const [open, setOpen] = useState(true);
  const isActive = node.id === activeId;
  const hasChildren = node.children.length > 0;

  return (
    <li>
      <div
        className={`group flex items-center gap-1 px-1.5 py-1 rounded cursor-pointer text-sm ${
          isActive ? "bg-surface text-text" : "hover:bg-surface text-text/90"
        }`}
        style={{ paddingLeft: 6 + depth * 12 }}
      >
        <button
          className="p-0.5 text-muted disabled:opacity-30"
          disabled={!hasChildren}
          onClick={(e) => {
            e.stopPropagation();
            setOpen((v) => !v);
          }}
          aria-label={open ? "Collapse" : "Expand"}
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
        <span className="flex-1 truncate" onClick={() => onSelect(node.id)}>
          {node.title}
        </span>
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
              onSelect={onSelect}
              onCreate={onCreate}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function filterTree(tree: TopicNode[], q: string): TopicNode[] {
  if (!q) return tree;
  const out: TopicNode[] = [];
  for (const node of tree) {
    const matched = node.title.toLowerCase().includes(q);
    const children = filterTree(node.children, q);
    if (matched || children.length > 0) {
      out.push({ ...node, children });
    }
  }
  return out;
}
