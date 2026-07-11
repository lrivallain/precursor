import { useState } from "react";
import { CalendarClock, FileText, Link2, Loader2, RefreshCw, Users } from "lucide-react";
import type { AgendaEvent, MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { Markdown } from "./Markdown";

interface Props {
  session: MeetingSession;
  onUpdated: (session: MeetingSession) => void;
  topicTitle: string | null;
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Context tab: an AI summary of the attached topic's conversation, plus the
 * user's M365 agenda (via WorkIQ) so they can link a meeting — folding its
 * invitees into the summary's attendees.
 */
export function ContextSection({ session, onUpdated, topicTitle }: Props) {
  const [topicSummary, setTopicSummary] = useState("");
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [events, setEvents] = useState<AgendaEvent[] | null>(null);
  const [agendaLoading, setAgendaLoading] = useState(false);
  const [agendaDetail, setAgendaDetail] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);

  const linked = session.external_meeting;

  async function loadTopicSummary(): Promise<void> {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const res = await api.topicContextSummary(session.id);
      setTopicSummary(res.summary);
    } catch (e) {
      setSummaryError(
        e instanceof Error ? e.message : "Couldn't summarize the topic.",
      );
    } finally {
      setSummaryLoading(false);
    }
  }

  async function loadAgenda(): Promise<void> {
    setAgendaLoading(true);
    setAgendaDetail(null);
    try {
      const res = await api.getAgenda();
      setEvents(res.events);
      if (!res.available) setAgendaDetail(res.detail ?? "Agenda unavailable.");
      else if (res.events.length === 0) setAgendaDetail("No upcoming meetings found.");
    } catch (e) {
      setAgendaDetail(e instanceof Error ? e.message : "Couldn't load the agenda.");
      setEvents([]);
    } finally {
      setAgendaLoading(false);
    }
  }

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
              onClick={() => void loadTopicSummary()}
              disabled={summaryLoading}
              className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
            >
              {summaryLoading ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <RefreshCw size={12} />
              )}
              {topicSummary ? "Refresh" : "Summarize topic"}
            </button>
          )}
        </div>
        {session.topic_id == null ? (
          <p className="text-sm text-muted">
            No topic attached. Pick one from the toolbar to bring its context in.
          </p>
        ) : summaryError ? (
          <p className="text-[12px] text-red-500">{summaryError}</p>
        ) : topicSummary ? (
          <Markdown>{topicSummary}</Markdown>
        ) : (
          <p className="text-sm text-muted">
            Summarize the conversation in <strong>{topicTitle ?? "the topic"}</strong>{" "}
            to brief yourself before the meeting.
          </p>
        )}
      </section>

      {/* Linked meeting / agenda */}
      <section className="p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <CalendarClock size={12} /> Meeting from your agenda
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
            {events ? "Refresh" : "Load agenda"}
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

        {agendaDetail && <p className="mb-2 text-[12px] text-muted">{agendaDetail}</p>}

        {events && events.length > 0 && (
          <ul className="space-y-1.5">
            {events.map((ev) => {
              const key = ev.id ?? ev.subject;
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
                    disabled={linking === key}
                    className="inline-flex shrink-0 items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
                  >
                    {linking === key ? <Loader2 size={11} className="animate-spin" /> : <Link2 size={11} />}
                    Link
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {!events && !agendaDetail && (
          <p className="text-sm text-muted">
            Load your Microsoft 365 agenda (via WorkIQ) to link a meeting — its
            invitees are added to the summary&apos;s attendees.
          </p>
        )}
      </section>
    </div>
  );
}
