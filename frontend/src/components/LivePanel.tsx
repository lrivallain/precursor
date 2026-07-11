import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Columns2, Square } from "lucide-react";
import { useResizableWidth } from "../lib/useResizableWidth";
import { ResizeHandle } from "./ResizeHandle";

export interface LiveTab {
  id: string;
  label: string;
  /** Optional count badge shown on the tab. */
  badge?: number | null;
}

interface Layout {
  split: boolean;
  left: string;
  right: string;
}

interface Props {
  tabs: LiveTab[];
  render: (id: string) => ReactNode;
  /** localStorage key for the persisted layout. */
  storageKey: string;
  /**
   * Ask the panel to focus a tab in the primary pane. Bump ``nonce`` to trigger
   * (e.g. focus Summary once it's generated).
   */
  focus?: { id: string; nonce: number };
}

function loadLayout(key: string, fallback: Layout): Layout {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw) return { ...fallback, ...(JSON.parse(raw) as Partial<Layout>) };
  } catch {
    /* ignore */
  }
  return fallback;
}

function TabStrip({
  tabs,
  active,
  onSelect,
  right,
}: {
  tabs: LiveTab[];
  active: string;
  onSelect: (id: string) => void;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-center gap-1 border-b border-border px-2 py-1">
      <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onSelect(t.id)}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded px-2.5 py-1 text-[12px] ${
              active === t.id ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface"
            }`}
          >
            {t.label}
            {t.badge != null && t.badge > 0 && (
              <span className="rounded-full bg-accent/20 px-1.5 text-[10px] tabular-nums text-accent">
                {t.badge}
              </span>
            )}
          </button>
        ))}
      </div>
      {right}
    </div>
  );
}

/**
 * A tabbed content panel with an optional side-by-side split: two panes, each
 * with its own tab-strip and a resizable divider. Layout (split + each pane's
 * active tab + divider width) is persisted globally.
 */
export function LivePanel({ tabs, render, storageKey, focus }: Props) {
  const ids = useMemo(() => tabs.map((t) => t.id), [tabs]);
  const fallback: Layout = {
    split: tabs.length > 1,
    left: tabs[0]?.id ?? "",
    right: tabs[1]?.id ?? tabs[0]?.id ?? "",
  };
  const [layout, setLayout] = useState<Layout>(() => loadLayout(storageKey, fallback));

  // Keep active ids valid as the available tabs change.
  useEffect(() => {
    setLayout((l) => {
      const left = ids.includes(l.left) ? l.left : (ids[0] ?? "");
      const right = ids.includes(l.right) ? l.right : (ids[1] ?? ids[0] ?? "");
      return left === l.left && right === l.right ? l : { ...l, left, right };
    });
  }, [ids]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify(layout));
  }, [storageKey, layout]);

  // External focus request → show it in the primary (left) pane.
  const lastNonce = useRef<number | null>(null);
  useEffect(() => {
    if (!focus || focus.nonce === lastNonce.current) return;
    lastNonce.current = focus.nonce;
    if (ids.includes(focus.id)) setLayout((l) => ({ ...l, left: focus.id }));
  }, [focus, ids]);

  const { width: leftWidth, onMouseDown: onDivider } = useResizableWidth({
    storageKey: `${storageKey}:leftWidth`,
    defaultWidth: 520,
    min: 220,
    max: 1200,
    side: "right",
  });

  const splitToggle = (
    <button
      type="button"
      onClick={() => setLayout((l) => ({ ...l, split: !l.split }))}
      aria-label={layout.split ? "Single pane" : "Split into two panes"}
      data-tooltip={layout.split ? "Single pane" : "Split view"}
      className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-accent"
    >
      {layout.split ? <Square size={14} /> : <Columns2 size={14} />}
    </button>
  );

  if (!layout.split) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <TabStrip
          tabs={tabs}
          active={layout.left}
          onSelect={(id) => setLayout((l) => ({ ...l, left: id }))}
          right={splitToggle}
        />
        <div className="min-h-0 flex-1">{render(layout.left)}</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1">
      <div
        className="relative flex min-w-0 flex-col border-r border-border"
        style={{ width: leftWidth }}
      >
        <TabStrip
          tabs={tabs}
          active={layout.left}
          onSelect={(id) => setLayout((l) => ({ ...l, left: id }))}
          right={splitToggle}
        />
        <div className="min-h-0 flex-1">{render(layout.left)}</div>
        <ResizeHandle onMouseDown={onDivider} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <TabStrip
          tabs={tabs}
          active={layout.right}
          onSelect={(id) => setLayout((l) => ({ ...l, right: id }))}
        />
        <div className="min-h-0 flex-1">{render(layout.right)}</div>
      </div>
    </div>
  );
}
