import { useEffect, useRef, type ReactNode } from "react";
import { History } from "lucide-react";
import type { AgendaEvent } from "../lib/types";

/**
 * The agenda split into "Past" and "Current & upcoming", scrolled so the "Now"
 * boundary sits at the top: past meetings stay one scroll up, current and future
 * ones are immediately actionable. Each caller supplies its own row (Start on
 * the live start screen, Link in the Context tab).
 */
export function MeetingAgendaList({
  past,
  upcoming,
  renderRow,
  upcomingIcon,
  maxHeight,
}: {
  past: AgendaEvent[];
  upcoming: AgendaEvent[];
  renderRow: (event: AgendaEvent, isPast: boolean) => ReactNode;
  upcomingIcon: ReactNode;
  /** Tailwind max-height class, e.g. ``max-h-64``. */
  maxHeight: string;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  const boundaryRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (past.length === 0 || upcoming.length === 0) return;
    const box = listRef.current;
    const marker = boundaryRef.current;
    if (box && marker) {
      box.scrollTop += marker.getBoundingClientRect().top - box.getBoundingClientRect().top;
    }
  }, [past.length, upcoming.length]);

  return (
    <div ref={listRef} className={`${maxHeight} space-y-1.5 overflow-y-auto`}>
      {past.length > 0 && (
        <>
          <div className="flex items-center gap-2 px-0.5 pt-0.5 text-[10px] font-medium uppercase tracking-wide text-amber-600 dark:text-amber-400">
            <History size={11} /> Past
            <span className="h-px flex-1 bg-amber-500/30" />
          </div>
          <ul className="space-y-1.5">{past.map((ev) => renderRow(ev, true))}</ul>
        </>
      )}
      {upcoming.length > 0 && (
        <>
          <div
            ref={boundaryRef}
            className="flex items-center gap-2 px-0.5 pt-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400"
          >
            {upcomingIcon} Current &amp; upcoming
            <span className="h-px flex-1 bg-emerald-500/30" />
          </div>
          <ul className="space-y-1.5">{upcoming.map((ev) => renderRow(ev, false))}</ul>
        </>
      )}
    </div>
  );
}
