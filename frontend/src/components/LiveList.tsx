import { useMemo, useState } from "react";
import { Radio, Search } from "lucide-react";
import type { MeetingSession } from "../lib/types";
import { InlineTitle } from "./InlineTitle";

interface LiveListProps {
  sessions: MeetingSession[] | null;
  activeId: number | null;
  /** Session currently recording (red dot), if any. */
  recordingId?: number | null;
  onSelect: (session: MeetingSession) => void;
  /** Rename a session (double-click its title). */
  onRename?: (session: MeetingSession, title: string) => void | Promise<void>;
}

/** Sidebar list of live meeting sessions (the "Live" section). */
export function LiveList({ sessions, activeId, recordingId, onSelect, onRename }: LiveListProps) {
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
              const isOpen = s.status === "active";
              return (
                <li key={s.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(s)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(s);
                      }
                    }}
                    className={`flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      isActive ? "bg-accent/15 text-accent" : "hover:bg-surface"
                    }`}
                  >
                    {isRecording ? (
                      <Radio
                        size={14}
                        className="shrink-0 animate-pulse text-red-500"
                        data-tooltip="Recording"
                        aria-label="Recording"
                      />
                    ) : isOpen ? (
                      <Radio
                        size={14}
                        className="shrink-0 text-green-500"
                        data-tooltip="Open"
                        aria-label="Open"
                      />
                    ) : (
                      <Radio
                        size={14}
                        className="shrink-0 text-muted opacity-60"
                        data-tooltip="Ended"
                        aria-label="Ended"
                      />
                    )}
                    <span className="flex-1 truncate">
                      {onRename ? (
                        <InlineTitle
                          title={s.title}
                          onRename={(t) => onRename(s, t)}
                          className="block truncate"
                        />
                      ) : (
                        s.title
                      )}
                    </span>
                    {s.status === "ended" && (
                      <span className="shrink-0 text-[10px] uppercase tracking-wide text-muted">
                        ended
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
