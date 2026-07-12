import { useMemo, useState } from "react";
import { Radio, Search } from "lucide-react";
import type { MeetingSession } from "../lib/types";

interface LiveListProps {
  sessions: MeetingSession[] | null;
  activeId: number | null;
  /** Session currently recording (red dot), if any. */
  recordingId?: number | null;
  onSelect: (session: MeetingSession) => void;
}

/** Sidebar list of live meeting sessions (the "Live" section). */
export function LiveList({ sessions, activeId, recordingId, onSelect }: LiveListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const list = sessions ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((s) => s.title.toLowerCase().includes(q));
  }, [sessions, query]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search sessions..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {sessions === null ? (
          <div className="px-2 py-4 text-sm text-muted">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">No sessions yet.</div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((s) => {
              const isActive = s.id === activeId;
              const isRecording = s.id === recordingId;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(s)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "bg-accent/15 text-accent" : "hover:bg-surface"
                    }`}
                  >
                    {isRecording ? (
                      <span
                        className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-red-500"
                        data-tooltip="Recording"
                        aria-label="Recording"
                      />
                    ) : isActive ? (
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full bg-green-500"
                        data-tooltip="Open"
                        aria-label="Open"
                      />
                    ) : (
                      <Radio
                        size={14}
                        className={`shrink-0 ${s.status === "active" ? "text-accent" : "opacity-60"}`}
                      />
                    )}
                    <span className="flex-1 truncate">{s.title}</span>
                    {s.status === "ended" && (
                      <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
                        ended
                      </span>
                    )}
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
