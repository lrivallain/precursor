import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { partitionMeetings } from "./agenda";
import { useM365Enabled } from "./settingsStore";
import type { AgendaEvent } from "./types";

/**
 * The user's M365 agenda (via WorkIQ), split into past and current/upcoming
 * meetings. It loads once WorkIQ is known to be on; without M365 there is no
 * calendar, so nothing is fetched and no sign-in is triggered (``enabled``
 * tells the caller to hide its picker).
 */
export function useAgenda() {
  const enabled = useM365Enabled();
  const [events, setEvents] = useState<AgendaEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<string | null>(null);

  async function reload(): Promise<void> {
    setLoading(true);
    setDetail(null);
    try {
      const res = await api.meetings.getAgenda();
      setEvents(res.events);
      if (!res.available) setDetail(res.detail ?? "Agenda unavailable.");
    } catch (e) {
      setDetail(e instanceof Error ? e.message : "Couldn't load the agenda.");
      setEvents([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (enabled) void reload();
  }, [enabled]);

  const { past, upcoming } = useMemo(() => partitionMeetings(events ?? []), [events]);
  return { enabled, events, past, upcoming, loading, detail, reload };
}
