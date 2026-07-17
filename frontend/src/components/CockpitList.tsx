import { useMemo, useState } from "react";
import { Gauge, Search } from "lucide-react";
import type { Cockpit, CockpitState } from "../lib/types";

interface CockpitListProps {
  cockpits: Cockpit[] | null;
  activeId: number | null;
  onSelect: (cockpit: Cockpit) => void;
}

// Small colored dot reflecting the live process state.
const STATE_DOT: Record<CockpitState, string> = {
  running: "bg-emerald-500",
  starting: "bg-amber-500 animate-pulse",
  unreachable: "bg-amber-500",
  crashed: "bg-red-500",
  stopped: "bg-muted/50",
};

export function CockpitStateDot({ state }: { state: CockpitState }) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${STATE_DOT[state]}`}
      title={state}
      aria-label={state}
    />
  );
}

export function CockpitList({ cockpits, activeId, onSelect }: CockpitListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = cockpits ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((c) => c.name.toLowerCase().includes(q));
  }, [cockpits, query]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search cockpits..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {cockpits === null ? (
          <div className="px-2 py-4 text-sm text-muted">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No cockpits yet.</div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((c) => {
              const isActive = c.id === activeId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(c)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "section-selected" : "hover:bg-surface"
                    }`}
                  >
                    <Gauge size={14} className="shrink-0 opacity-70" />
                    <span className="flex-1 truncate">{c.name}</span>
                    <CockpitStateDot state={c.status.state} />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
