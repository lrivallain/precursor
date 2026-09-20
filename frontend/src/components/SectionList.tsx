import { useState } from "react";
import type { ReactNode } from "react";
import { LayoutDashboard, Loader2, Search } from "lucide-react";
import { useScrollActiveIntoView } from "../lib/useScrollActiveIntoView";

export interface SectionListItem {
  id: number;
  label: string;
  detail: string;
  dot: string;
  unread?: number;
  group?: string;
}

interface Props {
  label: string;
  items: SectionListItem[];
  activeId: number | null;
  overviewSelected: boolean;
  loading: boolean;
  error?: string | null;
  emptyMessage: string;
  controls?: ReactNode;
  onRetry: () => void;
  onOverview: () => void;
  onSelect: (id: number) => void;
}

/** Compact navigation; monitoring and execution controls belong to the overview. */
export function SectionList({
  label,
  items,
  activeId,
  overviewSelected,
  loading,
  error,
  emptyMessage,
  controls,
  onRetry,
  onOverview,
  onSelect,
}: Props) {
  const [query, setQuery] = useState("");
  const activeItemRef = useScrollActiveIntoView<HTMLButtonElement>(activeId);
  const search = query.trim().toLowerCase();
  const filtered = items.filter((item) => item.label.toLowerCase().includes(search));
  const groups = new Map<string, SectionListItem[]>();
  for (const item of filtered) {
    const key = item.group ?? "";
    const group = groups.get(key) ?? [];
    group.push(item);
    groups.set(key, group);
  }

  return (
    <nav aria-label={`${label} list`} className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-border p-2">
        <button
          type="button"
          onClick={onOverview}
          aria-current={overviewSelected ? "page" : undefined}
          className={`flex w-full items-center gap-2 rounded px-2 py-2 text-sm ${
            overviewSelected ? "section-selected" : "hover:bg-surface"
          }`}
        >
          <LayoutDashboard size={16} />
          Overview
        </button>
      </div>
      <div className="border-b border-border px-3 py-2">
        <div className="relative">
          <Search size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            aria-label={`Search ${label.toLowerCase()}`}
            placeholder={`Search ${label.toLowerCase()}...`}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
        {controls && <div className="mt-2">{controls}</div>}
      </div>
      {error && (
        <div role="alert" className="border-b border-border p-3 text-sm">
          <p>{error}</p>
          <button type="button" onClick={onRetry} className="mt-2 rounded px-2 py-1 text-accent hover:bg-surface">
            Retry
          </button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {loading ? (
          <p role="status" className="flex items-center gap-2 px-2 py-4 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading {label.toLowerCase()}...
          </p>
        ) : filtered.length > 0 ? (
          [...groups].map(([group, groupItems]) => (
            <section key={group} aria-label={group || undefined} className={group ? "mb-3" : undefined}>
              {group && (
                <div className="flex items-center gap-2 px-2 py-1.5 text-[11px] text-muted">
                  <h2 className="font-semibold uppercase tracking-wide">{group}</h2>
                  <span className="rounded-full bg-surface px-1.5 tabular-nums">{groupItems.length}</span>
                </div>
              )}
              <ul className="space-y-0.5">
                {groupItems.map((item) => (
                  <li key={item.id}>
                    <button
                      ref={item.id === activeId ? activeItemRef : undefined}
                      type="button"
                      onClick={() => onSelect(item.id)}
                      aria-current={item.id === activeId ? "page" : undefined}
                      className={`flex w-full items-center gap-2 rounded px-2 py-2 text-left text-sm ${
                        item.id === activeId ? "section-selected" : "hover:bg-surface"
                      }`}
                    >
                      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${item.dot}`} />
                      <span className="min-w-0 flex-1">
                        <span className={`block truncate ${item.unread ? "font-semibold" : ""}`} data-tooltip={item.label}>
                          {item.label}
                        </span>
                        <span className="block truncate text-xs text-muted">{item.detail}</span>
                      </span>
                      {!!item.unread && item.id !== activeId && (
                        <span aria-label={`${item.unread} unread`} className="shrink-0 rounded-full bg-accent px-1.5 text-xs text-white">
                          {item.unread}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        ) : !error && (
          <div className="px-2 py-4 text-sm text-muted">
            {search ? (
              <>
                <p>No matching {label.toLowerCase()}.</p>
                <button type="button" onClick={() => setQuery("")} className="mt-2 rounded px-2 py-1 text-accent hover:bg-surface">
                  Clear search
                </button>
              </>
            ) : emptyMessage}
          </div>
        )}
      </div>
    </nav>
  );
}
