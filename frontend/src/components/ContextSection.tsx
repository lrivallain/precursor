import { useEffect, useState } from "react";
import { CalendarClock, FileText, Link2, Loader2, RefreshCw, Users } from "lucide-react";
import type { AgendaEvent, MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

interface Props {
  session: MeetingSession;
  onUpdated: (session: MeetingSession) => void;
  topicTitle: string | null;
  topicSummary: string;
  topicSummaryLoading: boolean;
  topicSummaryError: string | null;
  onRefreshTopicSummary: () => void;
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (sameDay) return time;
  const day = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  return `${day} ${time}`;
}

/**
 * Context tab: an AI summary of the attached topic's conversation (auto-generated
 * on link), plus today's M365 agenda (via WorkIQ) so the user can pick the
 * meeting for context — folding its invitees into the summary's attendees.
 */
export function ContextSection({
  session,
  onUpdated,
  topicTitle,
  topicSummary,
  topicSummaryLoading,
  topicSummaryError,
  onRefreshTopicSummary,
}: Props) {
  const [events, setEvents] = useState<AgendaEvent[] | null>(null);
  const [agendaLoading, setAgendaLoading] = useState(false);
  const [agendaDetail, setAgendaDetail] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);

  const linked = session.external_meeting;

  async function loadAgenda(): Promise<void> {
    setAgendaLoading(true);
    setAgendaDetail(null);
    try {
      const res = await api.getAgenda();
      setEvents(res.events);
      if (!res.available) setAgendaDetail(res.detail ?? "Agenda unavailable.");
      else if (res.events.length === 0) setAgendaDetail("No meetings on your calendar today.");
    } catch (e) {
      setAgendaDetail(e instanceof Error ? e.message : "Couldn't load the agenda.");
      setEvents([]);
    } finally {
      setAgendaLoading(false);
    }
  }

  // Auto-load today's agenda when the Context tab first mounts.
  useEffect(() => {
    void loadAgenda();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function link(event: AgendaEvent): Promise<void> {
    setLinking(event.id ?? event.subject);
    try {
      const updated = await api.linkMeeting(session.id, event);
      onUpdated(updated);
    } catch {
      /* non-fatal */
    } finally {
      setLinking(null);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Topic context */}
      <section className="border-b border-border p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <FileText size={12} /> Topic context
          </div>
          {session.topic_id != null && (
            <button
              type="button"
              onClick={onRefreshTopicSummary}
              disabled={topicSummaryLoading}
              className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
            >
              {topicSummaryLoading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <RefreshCw size={12} />
              )}
              Refresh
            </button>
          )}
        </div>
        {session.topic_id == null ? (
          <p className="text-sm text-muted">
            No topic attached. Pick one from the toolbar to bring its context in.
          </p>
        ) : topicSummaryError ? (
          <p className="text-[12px] text-red-500">{topicSummaryError}</p>
        ) : topicSummary ? (
          <Markdown>{topicSummary}</Markdown>
        ) : topicSummaryLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Summarizing{" "}
            {topicTitle ?? "the topic"}…
          </div>
        ) : (
          <p className="text-sm text-muted">No conversation to summarize yet.</p>
        )}
      </section>

      {/* Today's agenda */}
      <section className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <CalendarClock size={12} /> Today&apos;s meetings
          </div>
          <button
            type="button"
            onClick={() => void loadAgenda()}
            disabled={agendaLoading}
            className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
          >
            {agendaLoading ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
            Refresh
          </button>
        </div>

        {linked && (
          <div className="mb-3 rounded border border-accent/40 bg-accent/5 px-3 py-2">
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <Link2 size={13} className="text-accent" /> {linked.subject}
            </div>
            <div className="mt-0.5 text-[12px] text-muted">
              {formatWhen(linked.start)}
              {linked.attendees && linked.attendees.length > 0 && (
                <span className="ml-2 inline-flex items-center gap-1">
                  <Users size={11} /> {linked.attendees.length}
                </span>
              )}
            </div>
          </div>
        )}

        {agendaLoading && !events && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Loading your agenda…
          </div>
        )}
        {agendaDetail && <p className="mb-2 text-[12px] text-muted">{agendaDetail}</p>}

        {events && events.length > 0 && (
          <ul className="space-y-1.5">
            {events.map((ev) => {
              const key = ev.id ?? ev.subject;
              const isLinked = linked?.subject === ev.subject;
              return (
                <li
                  key={key}
                  className="flex items-center gap-2 rounded border border-border px-2.5 py-1.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{ev.subject}</div>
                    <div className="text-[11px] text-muted">
                      {formatWhen(ev.start)}
                      {ev.attendees.length > 0 && (
                        <span className="ml-2 inline-flex items-center gap-0.5">
                          <Users size={10} /> {ev.attendees.length}
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void link(ev)}
                    disabled={linking === key || isLinked}
                    className="inline-flex shrink-0 items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
                  >
                    {linking === key ? (
                      <Loader2 size={11} className="animate-spin" />
                    ) : (
                      <Link2 size={11} />
                    )}
                    {isLinked ? "Linked" : "Link"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
