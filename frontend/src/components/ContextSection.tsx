import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookmarkPlus,
  CalendarClock,
  Check,
  FileText,
  History,
  Link2,
  Link2Off,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import type { AgendaEvent, MeetingSession } from "../lib/types";
import { api } from "../lib/api";
import { formatMeetingWhen, partitionMeetings } from "../lib/agenda";
import { CopyableMarkdown } from "./CopyableMarkdown";
import { MeetingBody } from "./MeetingBody";

interface Props {
  session: MeetingSession;
  onUpdated: (session: MeetingSession) => void;
  topicTitle: string | null;
  topicSummary: string;
  topicSummaryLoading: boolean;
  topicSummaryError: string | null;
  onRefreshTopicSummary: () => void;
}

/**
 * Context tab: an AI summary of the attached topic's conversation (auto-generated
 * on link), plus the user's M365 agenda (via WorkIQ) so the user can pick the
 * meeting for context — folding its invitees into the summary's attendees. Past
 * meetings are included (and clearly marked) so a session can be attached to a
 * meeting that already happened.
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
  const [unlinking, setUnlinking] = useState(false);
  const [postingMeeting, setPostingMeeting] = useState(false);
  const [postedMeeting, setPostedMeeting] = useState(false);
  const [postMeetingError, setPostMeetingError] = useState<string | null>(null);

  const linked = session.external_meeting;
  const canPost = session.topic_id != null;

  const { past, upcoming } = useMemo(() => partitionMeetings(events ?? []), [events]);

  // Auto-scroll the meeting list so the "Current & upcoming" boundary sits at the
  // top — past meetings stay one scroll up, current/future ones are front-and-centre.
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

  // One agenda row → a "Link" button that attaches the meeting for context.
  // ``isPast`` renders it muted (a meeting that already happened, e.g. to
  // summarize from its Teams transcript).
  function renderRow(ev: AgendaEvent, isPast: boolean): React.ReactNode {
    const key = ev.id ?? ev.subject;
    return (
      <li
        key={key}
        className={`flex items-center gap-2 rounded border border-border px-2.5 py-1.5 ${
          isPast ? "opacity-80" : ""
        }`}
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm">{ev.subject}</div>
          <div className="text-[11px] text-muted">
            {formatMeetingWhen(ev.start)}
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
          data-tooltip={isPast ? "Attach this past meeting for context" : undefined}
          className="inline-flex shrink-0 items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
        >
          {linking === key ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Link2 size={11} />
          )}
          Link
        </button>
      </li>
    );
  }

  async function loadAgenda(): Promise<void> {
    setAgendaLoading(true);
    setAgendaDetail(null);
    try {
      const res = await api.meetings.getAgenda();
      setEvents(res.events);
      if (!res.available) setAgendaDetail(res.detail ?? "Agenda unavailable.");
      else if (res.events.length === 0) setAgendaDetail("No meetings on your calendar.");
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
      const updated = await api.meetings.link(session.id, event);
      onUpdated(updated);
    } catch {
      /* non-fatal */
    } finally {
      setLinking(null);
    }
  }

  async function unlink(): Promise<void> {
    setUnlinking(true);
    try {
      const updated = await api.meetings.unlink(session.id);
      onUpdated(updated);
    } catch {
      /* non-fatal */
    } finally {
      setUnlinking(false);
    }
  }

  async function postMeeting(): Promise<void> {
    if (postingMeeting || !canPost) return;
    setPostingMeeting(true);
    setPostMeetingError(null);
    try {
      await api.meetings.postToTopic(session.id);
      setPostedMeeting(true);
      setTimeout(() => setPostedMeeting(false), 2000);
    } catch (e) {
      setPostMeetingError(e instanceof Error ? e.message : "Couldn't post to the topic.");
    } finally {
      setPostingMeeting(false);
    }
  }

  async function removeNote(index: number): Promise<void> {
    const next = (session.context_notes ?? []).filter((_, i) => i !== index);
    try {
      const updated = await api.meetings.setContextNotes(session.id, next);
      onUpdated(updated);
    } catch {
      /* non-fatal */
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
          <CopyableMarkdown>{topicSummary}</CopyableMarkdown>
        ) : topicSummaryLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Summarizing{" "}
            {topicTitle ?? "the topic"}…
          </div>
        ) : (
          <p className="text-sm text-muted">No conversation to summarize yet.</p>
        )}
      </section>

      {/* Pinned context notes (e.g. saved Q&A answers) */}
      {session.context_notes && session.context_notes.length > 0 && (
        <section className="border-b border-border p-4">
          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
            <BookmarkPlus size={12} /> Pinned context
          </div>
          <ul className="space-y-1.5">
            {session.context_notes.map((note, i) => (
              <li
                key={`${i}-${note.slice(0, 24)}`}
                className="flex items-start gap-2 rounded border border-border px-2.5 py-1.5 text-[13px]"
              >
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{note}</span>
                <button
                  type="button"
                  onClick={() => void removeNote(i)}
                  aria-label="Remove note"
                  data-tooltip="Remove from context"
                  className="shrink-0 text-muted hover:text-red-500"
                >
                  <X size={13} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Linked meeting (context grounding) or today's agenda picker */}
      <section className="p-4">
        {linked ? (
          <>
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                <Link2 size={12} /> Linked meeting
              </div>
              <button
                type="button"
                onClick={() => void unlink()}
                disabled={unlinking}
                className="inline-flex items-center gap-1.5 rounded border border-border px-2 py-1 text-[12px] hover:bg-surface disabled:opacity-50"
              >
                {unlinking ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Link2Off size={12} />
                )}
                Unlink
              </button>
            </div>

            <div className="rounded border border-accent/40 bg-accent/5 px-3 py-3">
              <div className="flex items-center gap-1.5 text-sm font-medium">
                <CalendarClock size={13} className="text-accent" /> {linked.subject}
              </div>
              <div className="mt-1 space-y-0.5 text-[12px] text-muted">
                {(linked.start || linked.end) && (
                  <div>
                    {formatMeetingWhen(linked.start)}
                    {linked.end && ` – ${formatMeetingWhen(linked.end)}`}
                  </div>
                )}
                {linked.organizer && <div>Organizer: {linked.organizer}</div>}
              </div>

              {linked.attendees && linked.attendees.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {linked.attendees.map((a, i) => (
                    <span
                      key={`${a.email ?? a.name}-${i}`}
                      className="inline-flex items-center gap-1 rounded-full border border-border bg-bg px-2 py-0.5 text-[11px]"
                    >
                      <Users size={10} /> {a.name || a.email}
                    </span>
                  ))}
                </div>
              )}

              {linked.body && (
                <div className="mt-3">
                  <MeetingBody html={linked.body} />
                </div>
              )}

              <div className="mt-3 flex items-start gap-1.5 rounded bg-bg/60 px-2 py-1.5 text-[11px] text-muted">
                <ShieldCheck size={13} className="mt-px shrink-0 text-accent" />
                <span>
                  This meeting grounds the transcript, live insights and summary as
                  additional context.
                </span>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void postMeeting()}
                  disabled={postingMeeting || !canPost}
                  data-tooltip={canPost ? undefined : "Attach a topic to post"}
                  className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1.5 text-[12px] text-white disabled:opacity-50"
                >
                  {postedMeeting ? <Check size={13} /> : <Send size={13} />}
                  {postedMeeting
                    ? "Posted"
                    : postingMeeting
                      ? "Posting…"
                      : "Post to topic"}
                </button>
                {postMeetingError && (
                  <span className="text-[11px] text-red-500">{postMeetingError}</span>
                )}
              </div>
            </div>
          </>
        ) : (
          <>
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
                <CalendarClock size={12} /> Your meetings
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

            {agendaLoading && !events && (
              <div className="flex items-center gap-2 text-sm text-muted">
                <Loader2 size={14} className="animate-spin" /> Loading your agenda…
              </div>
            )}
            {agendaDetail && <p className="mb-2 text-[12px] text-muted">{agendaDetail}</p>}

            {(past.length > 0 || upcoming.length > 0) && (
              <div ref={listRef} className="max-h-72 space-y-1.5 overflow-y-auto">
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
                      <CalendarClock size={11} /> Current &amp; upcoming
                      <span className="h-px flex-1 bg-emerald-500/30" />
                    </div>
                    <ul className="space-y-1.5">{upcoming.map((ev) => renderRow(ev, false))}</ul>
                  </>
                )}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
