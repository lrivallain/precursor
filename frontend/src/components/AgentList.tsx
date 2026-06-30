import { useMemo, useState } from "react";
import { Bot, CalendarClock, Search } from "lucide-react";
import type { AgentSession } from "../lib/types";
import { AgentStatusBadge, agentRelativeTime } from "./AgentStatusBadge";
import { InlineTitle } from "./InlineTitle";

interface AgentListProps {
  agents: AgentSession[];
  activeId: number | null;
  enabled: boolean;
  onSelect: (id: number) => void;
  onRename: (id: number, title: string) => void | Promise<void>;
}

export function AgentList({ agents, activeId, enabled, onSelect, onRename }: AgentListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return agents;
    return agents.filter((a) => a.title.toLowerCase().includes(q));
  }, [agents, query]);

  if (!enabled) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <Bot size={20} className="text-muted" />
        <p className="text-[11px] text-muted">
          Agents mode is off. Enable it in Settings → Agents.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="border-b border-border px-3 py-2">
        <div className="relative">
          <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-muted" />
          <input
            type="search"
            placeholder="Search agents..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full rounded border border-border bg-surface py-1.5 pl-7 pr-2 text-sm outline-none focus:border-accent"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {filtered.length === 0 ? (
          <div className="px-2 py-4 text-sm text-muted">
            No agent sessions yet. Use “New” to start one.
          </div>
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((a) => {
              const isActive = a.id === activeId;
              return (
                <li key={a.id}>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => onSelect(a.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onSelect(a.id);
                      }
                    }}
                    className={`group w-full cursor-pointer rounded px-2 py-1.5 text-left ${
                      isActive ? "bg-accent/15 text-accent" : "hover:bg-surface"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Bot size={14} className="shrink-0 opacity-70" />
                      <InlineTitle
                        title={a.title}
                        onRename={(t) => onRename(a.id, t)}
                        className="flex-1 truncate text-sm"
                      />
                      {a.schedule && (
                        <CalendarClock
                          size={12}
                          className={`shrink-0 ${
                            a.schedule.enabled ? "text-accent" : "text-muted opacity-60"
                          }`}
                          aria-label={
                            a.schedule.enabled ? "Scheduled" : "Schedule paused"
                          }
                          data-tooltip={
                            a.schedule.enabled ? "Runs on a schedule" : "Schedule paused"
                          }
                        />
                      )}
                      <AgentStatusBadge status={a.status} />
                    </div>
                    <div className="mt-0.5 pl-6 text-[10px] text-muted">
                      {agentRelativeTime(a.last_activity_at ?? a.created_at)}
                      {a.topic_id != null && ` · topic #${a.topic_id}`}
                    </div>
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
