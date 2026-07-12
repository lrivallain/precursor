import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Columns2 } from "lucide-react";
import { useResizableWidth } from "../lib/useResizableWidth";
import { ResizeHandle } from "./ResizeHandle";

export interface LiveTab {
  id: string;
  label: string;
  /** Optional count badge shown on the tab. */
  badge?: number | null;
}

interface Pane {
  tabs: string[];
  active: string;
}

interface Layout {
  panes: Pane[];
}

interface Props {
  tabs: LiveTab[];
  render: (id: string) => ReactNode;
  /** localStorage key for the persisted layout. */
  storageKey: string;
  /**
   * Ask the panel to focus a tab (in whichever pane owns it). Bump ``nonce`` to
   * trigger (e.g. focus Summary once it's generated).
   */
  focus?: { id: string; nonce: number };
}

interface DragPayload {
  tabId: string;
  from: number;
}

const MIME = "application/x-live-tab";

// Keep panes consistent with the available tab ids: drop unknown ids, append
// unassigned ones to the first pane, fix each pane's active, and collapse any
// empty pane. Guarantees ≥ 1 pane, each with ≥ 1 tab and a valid active.
function normalize(panes: Pane[], ids: string[]): Pane[] {
  const idSet = new Set(ids);
  const assigned = new Set<string>();
  let cleaned: Pane[] = panes
    .map((p) => {
      const tabs = p.tabs.filter((t) => idSet.has(t) && !assigned.has(t));
      for (const t of tabs) assigned.add(t);
      return { tabs, active: p.active };
    })
    .filter((p) => p.tabs.length > 0);

  if (cleaned.length === 0) cleaned = [{ tabs: [], active: ids[0] ?? "" }];

  // Append any not-yet-assigned tabs to the first pane.
  const missing = ids.filter((t) => !assigned.has(t));
  if (missing.length) cleaned[0] = { ...cleaned[0], tabs: [...cleaned[0].tabs, ...missing] };

  return cleaned.map((p) => ({
    tabs: p.tabs,
    active: p.tabs.includes(p.active) ? p.active : p.tabs[0],
  }));
}

function loadLayout(key: string, ids: string[]): Layout {
  const fallback: Layout = { panes: [{ tabs: ids, active: ids[0] ?? "" }] };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Layout>;
      if (Array.isArray(parsed.panes) && parsed.panes.length > 0) {
        return { panes: normalize(parsed.panes as Pane[], ids) };
      }
    }
  } catch {
    /* ignore malformed layout */
  }
  return { panes: normalize(fallback.panes, ids) };
}

function TabStrip({
  paneIndex,
  pane,
  tabMeta,
  onSelect,
  onDropTab,
  right,
}: {
  paneIndex: number;
  pane: Pane;
  tabMeta: Map<string, LiveTab>;
  onSelect: (id: string) => void;
  onDropTab: (payload: DragPayload, to: number, beforeId: string | null) => void;
  right?: ReactNode;
}) {
  const [over, setOver] = useState(false);

  function readPayload(e: React.DragEvent): DragPayload | null {
    const raw = e.dataTransfer.getData(MIME);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as DragPayload;
    } catch {
      return null;
    }
  }

  return (
    <div
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(MIME)) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        setOver(false);
        const p = readPayload(e);
        if (p) {
          e.preventDefault();
          onDropTab(p, paneIndex, null);
        }
      }}
      className={`flex items-center gap-1 border-b px-2 py-1 ${
        over ? "border-accent bg-accent/5" : "border-border"
      }`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
        {pane.tabs.map((id) => {
          const meta = tabMeta.get(id);
          if (!meta) return null;
          return (
            <button
              key={id}
              type="button"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData(MIME, JSON.stringify({ tabId: id, from: paneIndex }));
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(e) => {
                if (e.dataTransfer.types.includes(MIME)) e.preventDefault();
              }}
              onDrop={(e) => {
                const p = readPayload(e);
                if (p) {
                  e.preventDefault();
                  e.stopPropagation();
                  setOver(false);
                  onDropTab(p, paneIndex, id);
                }
              }}
              onClick={() => onSelect(id)}
              className={`inline-flex shrink-0 cursor-grab items-center gap-1.5 rounded px-2.5 py-1 text-[12px] active:cursor-grabbing ${
                pane.active === id ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface"
              }`}
            >
              {meta.label}
              {meta.badge != null && meta.badge > 0 && (
                <span className="rounded-full bg-accent/20 px-1.5 text-[10px] tabular-nums text-accent">
                  {meta.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
      {right}
    </div>
  );
}

/**
 * A tabbed content panel with drag-and-drop between two side-by-side panes:
 * each tab lives in exactly one pane; drag a tab onto the other pane's strip to
 * move it, or onto a tab to reorder. Splitting seeds a second pane; emptying a
 * pane collapses back to one. Layout is persisted globally.
 */
export function LivePanel({ tabs, render, storageKey, focus }: Props) {
  const ids = useMemo(() => tabs.map((t) => t.id), [tabs]);
  const idsKey = ids.join("|");
  const tabMeta = useMemo(() => new Map(tabs.map((t) => [t.id, t])), [tabs]);

  const [layout, setLayout] = useState<Layout>(() => loadLayout(storageKey, ids));

  // Reconcile when the set of available tabs changes.
  useEffect(() => {
    setLayout((l) => ({ panes: normalize(l.panes, ids) }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify(layout));
  }, [storageKey, layout]);

  // External focus request → activate the tab in whichever pane owns it.
  const lastNonce = useRef<number | null>(null);
  useEffect(() => {
    if (!focus || focus.nonce === lastNonce.current) return;
    lastNonce.current = focus.nonce;
    setLayout((l) => {
      const panes = l.panes.map((p) =>
        p.tabs.includes(focus.id) ? { ...p, active: focus.id } : p,
      );
      return { panes };
    });
  }, [focus]);

  const { width: leftWidth, onMouseDown: onDivider } = useResizableWidth({
    storageKey: `${storageKey}:leftWidth`,
    defaultWidth: 520,
    min: 220,
    max: 1400,
    side: "right",
  });

  const split = layout.panes.length > 1;

  function selectTab(paneIndex: number, id: string): void {
    setLayout((l) => ({
      panes: l.panes.map((p, i) => (i === paneIndex ? { ...p, active: id } : p)),
    }));
  }

  function moveTab(payload: DragPayload, to: number, beforeId: string | null): void {
    setLayout((l) => {
      const panes = l.panes.map((p) => ({ tabs: [...p.tabs], active: p.active }));
      const from = payload.from;
      if (from < 0 || from >= panes.length || to < 0 || to >= panes.length) return l;
      const src = panes[from];
      const idx = src.tabs.indexOf(payload.tabId);
      if (idx === -1) return l;
      // No-op when dropping onto itself.
      if (from === to && (beforeId === payload.tabId || beforeId === null)) {
        if (beforeId === null) {
          return { panes: panes.map((p, i) => (i === to ? { ...p, active: payload.tabId } : p)) };
        }
      }
      src.tabs.splice(idx, 1);
      const dst = panes[to];
      let insertAt = dst.tabs.length;
      if (beforeId) {
        const bi = dst.tabs.indexOf(beforeId);
        if (bi !== -1) insertAt = bi;
      }
      dst.tabs.splice(insertAt, 0, payload.tabId);
      dst.active = payload.tabId;
      if (src.active === payload.tabId && src.tabs.length) src.active = src.tabs[0];
      const pruned = panes.filter((p) => p.tabs.length > 0);
      return { panes: pruned.length ? pruned : [{ tabs: ids, active: ids[0] ?? "" }] };
    });
  }

  function toggleSplit(): void {
    setLayout((l) => {
      if (l.panes.length > 1) {
        // Merge everything back into one pane.
        const tabsAll = l.panes.flatMap((p) => p.tabs);
        return { panes: [{ tabs: tabsAll, active: l.panes[0].active }] };
      }
      const only = l.panes[0];
      if (only.tabs.length < 2) return l; // nothing to split
      // Keep the active tab on the left; everything else moves right.
      const leftTabs = [only.active];
      const rightTabs = only.tabs.filter((t) => t !== only.active);
      return {
        panes: [
          { tabs: leftTabs, active: only.active },
          { tabs: rightTabs, active: rightTabs[0] },
        ],
      };
    });
  }

  const splitToggle = (
    <button
      type="button"
      onClick={toggleSplit}
      aria-label={split ? "Merge panes" : "Split into two panes"}
      data-tooltip={split ? "Merge panes" : "Split view"}
      className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-accent"
    >
      <Columns2 size={14} className={split ? "text-accent" : ""} />
    </button>
  );

  if (!split) {
    const pane = layout.panes[0];
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <TabStrip
          paneIndex={0}
          pane={pane}
          tabMeta={tabMeta}
          onSelect={(id) => selectTab(0, id)}
          onDropTab={moveTab}
          right={splitToggle}
        />
        <div className="min-h-0 flex-1">{render(pane.active)}</div>
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
          paneIndex={0}
          pane={layout.panes[0]}
          tabMeta={tabMeta}
          onSelect={(id) => selectTab(0, id)}
          onDropTab={moveTab}
          right={splitToggle}
        />
        <div className="min-h-0 flex-1">{render(layout.panes[0].active)}</div>
        <ResizeHandle onMouseDown={onDivider} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <TabStrip
          paneIndex={1}
          pane={layout.panes[1]}
          tabMeta={tabMeta}
          onSelect={(id) => selectTab(1, id)}
          onDropTab={moveTab}
        />
        <div className="min-h-0 flex-1">{render(layout.panes[1].active)}</div>
      </div>
    </div>
  );
}
