import { useEffect, useMemo, useState } from "react";
import { Radio, Search } from "lucide-react";
import type { MeetingSession } from "../lib/types";
import { InlineTitle } from "./InlineTitle";
import { useMultiSelect } from "../lib/useMultiSelect";
import { SelectToggleButton, SelectionToolbar, SelectionCheckbox } from "./ListSelection";

interface LiveListProps {
  sessions: MeetingSession[] | null;
  activeId: number | null;
  /** Session currently recording (red dot), if any. */
  recordingId?: number | null;
  onSelect: (session: MeetingSession) => void;
  /** Rename a session (double-click its title). */
  onRename?: (session: MeetingSession, title: string) => void | Promise<void>;
  /** Bulk-archive the selected sessions. Enables multi-select when provided. */
  onArchiveMany?: (ids: number[]) => void | Promise<void>;
}

/** Sidebar list of live meeting sessions (the "Live" section). */
export function LiveList({
  sessions,
  activeId,
  recordingId,
  onSelect,
  onRename,
  onArchiveMany,
}: LiveListProps) {
  const [query, setQuery] = useState("");
  const sel = useMultiSelect();
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(() => {
    const list = sessions ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter((s) => s.title.toLowerCase().includes(q));
  }, [sessions, query]);

  // Drop selected ids that vanished (archived elsewhere, deleted, filtered out).
  const filteredIds = useMemo(() => filtered.map((s) => s.id), [filtered]);
  useEffect(() => {
    if (sel.active) sel.prune(filteredIds);
  }, [filteredIds, sel]);

  const allSelected = filteredIds.length > 0 && filteredIds.every((id) => sel.isSelected(id));

  async function archiveSelected(): Promise<void> {
    if (!onArchiveMany || sel.count === 0) return;
    setBusy(true);
    try {
      await onArchiveMany([...sel.selected]);
      sel.exit();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
            <input
              type="search"
              placeholder="Search sessions..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
            />
          </div>
          {onArchiveMany && !sel.active && (filtered.length > 0 || sessions === null) && (
            <SelectToggleButton onClick={() => sel.enter()} />
          )}
        </div>
      </div>

      {sel.active && (
        <SelectionToolbar
          count={sel.count}
          allSelected={allSelected}
          onToggleAll={() => sel.toggleAll(filteredIds)}
          onArchive={() => void archiveSelected()}
          onCancel={() => sel.exit()}
          busy={busy}
        />
      )}

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
              const selected = sel.isSelected(s.id);
              return (
                <li key={s.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => (sel.active ? sel.toggle(s.id) : onSelect(s))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        if (sel.active) sel.toggle(s.id);
                        else onSelect(s);
                      }
                    }}
                    className={`flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left text-sm ${
                      sel.active && selected
                        ? "bg-accent/15"
                        : isActive && !sel.active
                          ? "section-selected"
                          : "hover:bg-surface"
                    }`}
                  >
                    {sel.active ? (
                      <SelectionCheckbox checked={selected} />
                    ) : isRecording ? (
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
                      {onRename && !sel.active ? (
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
