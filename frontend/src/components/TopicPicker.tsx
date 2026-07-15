import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, Search } from "lucide-react";
import type { Topic } from "../lib/types";

// Local tree node so the picker can render the topic hierarchy from a flat
// ``Topic[]`` (each caller passes the same flat list it already has).
interface PickerNode extends Topic {
  children: PickerNode[];
}

/**
 * A searchable topic lookup (combobox) that shows topics as a collapsible
 * tree, mirroring the sidebar hierarchy. Originally built to associate an agent
 * with a topic; shared so the Live meeting session picker matches it exactly.
 */
export function TopicPicker({
  topics,
  value,
  onChange,
  disabled,
}: {
  topics: Topic[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<ReadonlySet<number>>(new Set());
  const ref = useRef<HTMLDivElement>(null);
  const current = topics.find((t) => t.id === value) ?? null;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const tree = useMemo(() => buildTree(topics), [topics]);

  const q = query.trim().toLowerCase();
  const searching = q.length > 0;
  const visible = useMemo(() => (searching ? filterTree(tree, q) : tree), [tree, q, searching]);

  const toggle = (id: number) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const renderNode = (node: PickerNode, depth: number) => {
    const hasChildren = node.children.length > 0;
    // While searching we force every matched branch open so matches deep in the
    // tree stay reachable regardless of the persisted collapsed state.
    const isOpen = searching || !collapsed.has(node.id);
    return (
      <li key={node.id}>
        <div className="flex items-center gap-0.5" style={{ paddingLeft: depth * 12 }}>
          {hasChildren ? (
            <button
              type="button"
              aria-label={isOpen ? "Collapse" : "Expand"}
              onClick={() => toggle(node.id)}
              className="shrink-0 rounded p-0.5 text-muted hover:bg-bg hover:text-text"
            >
              {isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
          ) : (
            <span className="inline-block w-[18px] shrink-0" />
          )}
          <button
            type="button"
            onClick={() => {
              onChange(node.id);
              setOpen(false);
            }}
            className={`flex flex-1 items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg ${
              value === node.id ? "text-accent" : ""
            }`}
          >
            <span className="truncate">{node.title}</span>
            {value === node.id && <Check size={12} className="shrink-0" />}
          </button>
        </div>
        {hasChildren && isOpen && (
          <ul>{node.children.map((child) => renderNode(child, depth + 1))}</ul>
        )}
      </li>
    );
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setQuery("");
          setOpen((v) => !v);
        }}
        className="flex items-center gap-1 rounded border border-border bg-bg px-2 py-1 text-[11px] disabled:opacity-50"
      >
        <Search size={11} className="text-muted" />
        <span className={current ? "" : "text-muted"}>{current ? current.title : "None"}</span>
        <ChevronDown size={11} className="text-muted" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-60 rounded-md border border-border bg-surface shadow-lg">
          <div className="border-b border-border p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search topics…"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto p-1 text-[11px]">
            <li>
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left hover:bg-bg ${
                  value === null ? "text-accent" : "text-muted"
                }`}
              >
                None {value === null && <Check size={12} />}
              </button>
            </li>
            {visible.map((node) => renderNode(node, 0))}
            {visible.length === 0 && (
              <li className="px-2 py-1.5 text-muted">No matching topics.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

// Rebuild the parent/child hierarchy from a flat topic list. Topics whose
// parent isn't in the list (e.g. a filtered subset) surface as roots so nothing
// is dropped.
function buildTree(topics: Topic[]): PickerNode[] {
  const byId = new Map<number, PickerNode>();
  for (const t of topics) byId.set(t.id, { ...t, children: [] });
  const roots: PickerNode[] = [];
  for (const node of byId.values()) {
    const parent = node.parent_id != null ? byId.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

// Keep branches that match the query. A matching node keeps its full subtree so
// users can drill into descendants; otherwise ancestors are retained only when
// a descendant matches. Mirrors the sidebar's filterTree behavior.
function filterTree(tree: PickerNode[], q: string): PickerNode[] {
  const out: PickerNode[] = [];
  for (const node of tree) {
    const matched = node.title.toLowerCase().includes(q);
    const children = matched ? node.children : filterTree(node.children, q);
    if (matched || children.length > 0) out.push({ ...node, children });
  }
  return out;
}
