import { useEffect, useMemo, useState } from "react";
import { CalendarClock, Loader2, Radio, RefreshCw, Users } from "lucide-react";
import type {
  AgendaEvent,
  MeetingSession,
  MeetingSessionCreate,
  TopicNode,
} from "../lib/types";
import { useSettings } from "../lib/settingsStore";
import { api } from "../lib/api";
import { TopicPicker } from "./TopicPicker";
import { Select } from "./Select";

// Common BCP-47 tags offered in the create form. An empty value means "use the
// configured Azure Speech default"; the meeting can still be switched later.
const LANGUAGES: { value: string; label: string }[] = [
  { value: "", label: "Use configured default" },
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "fr-FR", label: "French (France)" },
  { value: "de-DE", label: "German" },
  { value: "es-ES", label: "Spanish (Spain)" },
  { value: "it-IT", label: "Italian" },
  { value: "pt-PT", label: "Portuguese (Portugal)" },
  { value: "nl-NL", label: "Dutch" },
];

// Flatten the topic tree into a flat list for the searchable topic picker.
function flattenTopicNodes(tree: TopicNode[]): TopicNode[] {
  const out: TopicNode[] = [];
  const walk = (nodes: TopicNode[]): void => {
    for (const n of nodes) {
      out.push(n);
      if (n.children.length) walk(n.children);
    }
  };
  walk(tree);
  return out;
}

// Compact "when" label for an agenda entry: just the time for today's meetings,
// or a short weekday+date prefix otherwise.
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

// Keep only meetings that are ongoing or still to come (already-finished ones
// are dropped), and flag the ones happening right now.
function isCurrentOrUpcoming(ev: AgendaEvent, now: number): boolean {
  const endMs = ev.end ? new Date(ev.end).getTime() : NaN;
  const startMs = ev.start ? new Date(ev.start).getTime() : NaN;
  if (!Number.isNaN(endMs)) return endMs >= now;
  if (!Number.isNaN(startMs)) return startMs >= now;
  return true;
}

function isOngoing(ev: AgendaEvent, now: number): boolean {
  const startMs = ev.start ? new Date(ev.start).getTime() : NaN;
  const endMs = ev.end ? new Date(ev.end).getTime() : NaN;
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return false;
  return startMs <= now && now < endMs;
}

/**
 * Landing surface shown in the Live pane when no session is selected. Creates a
 * meeting session with an optional attached topic and language, then hands the
 * new session back so the caller can open it.
 */
export function LiveStartHero({
  topics,
  onCreated,
}: {
  topics: TopicNode[];
  onCreated: (session: MeetingSession) => void | Promise<void>;
}) {
  const settings = useSettings();
  const sttReady = settings?.stt_azure_ready ?? false;
  const defaultLang = settings?.azure_speech_language || "";

  const [title, setTitle] = useState("");
  const [topicId, setTopicId] = useState<number | null>(null);
  const [language, setLanguage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Current & upcoming meetings from the user's M365 agenda (via WorkIQ), the
  // same source the in-session Context tab uses to link a meeting.
  const [events, setEvents] = useState<AgendaEvent[] | null>(null);
  const [agendaLoading, setAgendaLoading] = useState(false);
  const [agendaDetail, setAgendaDetail] = useState<string | null>(null);
  const [startingFrom, setStartingFrom] = useState<string | null>(null);

  const allTopics = useMemo(() => flattenTopicNodes(topics), [topics]);
  const languageOptions = useMemo(
    () =>
      LANGUAGES.map((l) =>
        l.value === "" && defaultLang
          ? { value: "", label: `Use configured default (${defaultLang})` }
          : l,
      ),
    [defaultLang],
  );

  const upcoming = useMemo(() => {
    if (!events) return null;
    const now = Date.now();
    return events.filter((ev) => isCurrentOrUpcoming(ev, now));
  }, [events]);

  async function loadAgenda(): Promise<void> {
    setAgendaLoading(true);
    setAgendaDetail(null);
    try {
      const res = await api.getAgenda();
      setEvents(res.events);
      if (!res.available) setAgendaDetail(res.detail ?? "Agenda unavailable.");
    } catch (e) {
      setAgendaDetail(e instanceof Error ? e.message : "Couldn't load the agenda.");
      setEvents([]);
    } finally {
      setAgendaLoading(false);
    }
  }

  // Load today's agenda when the welcome screen first mounts.
  useEffect(() => {
    void loadAgenda();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    const payload: MeetingSessionCreate = {
      title: title.trim() || null,
      topic_id: topicId,
      language: language || null,
    };
    try {
      const session = await api.createMeetingSession(payload);
      await onCreated(session);
      setTitle("");
      setTopicId(null);
      setLanguage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the session");
    } finally {
      setBusy(false);
    }
  }

  // Spin up a session pre-titled after the calendar meeting, then link it so its
  // invitees/context ground the transcript straight away.
  async function createFromMeeting(event: AgendaEvent): Promise<void> {
    if (busy || startingFrom) return;
    const key = event.id ?? event.subject;
    setStartingFrom(key);
    setError(null);
    try {
      const session = await api.createMeetingSession({
        title: event.subject.trim() || null,
        topic_id: topicId,
        language: language || null,
      });
      let linked = session;
      try {
        linked = await api.linkMeeting(session.id, event);
      } catch {
        /* linking is best-effort; keep the created session either way */
      }
      await onCreated(linked);
      setTitle("");
      setTopicId(null);
      setLanguage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start from the meeting");
    } finally {
      setStartingFrom(null);
    }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-4 overflow-y-auto p-8">
      <div className="flex items-center gap-2">
        <Radio size={18} />
        <h2 className="text-sm font-medium">Start a live meeting session</h2>
      </div>
      <p className="text-[12px] text-muted">
        Record an ongoing meeting to build notes, surface live insights, and
        optionally attach a summary to a topic. Audio is transcribed locally and
        never stored — only the transcript and insights are kept.
      </p>

      {!sttReady && (
        <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
          Azure Speech isn&apos;t configured yet. You can still create a session,
          but transcription needs a Speech key + endpoint in Settings.
        </div>
      )}

      {/* Current & upcoming meetings from the calendar — one click starts a
          session already linked to the meeting for context. */}
      {(agendaLoading || (upcoming && upcoming.length > 0) || agendaDetail) && (
        <section className="rounded border border-border bg-surface/40 p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              <CalendarClock size={12} /> Current &amp; upcoming meetings
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

          {upcoming && upcoming.length > 0 ? (
            <ul className="max-h-56 space-y-1.5 overflow-y-auto">
              {upcoming.map((ev) => {
                const key = ev.id ?? ev.subject;
                const ongoing = isOngoing(ev, Date.now());
                return (
                  <li
                    key={key}
                    className="flex items-center gap-2 rounded border border-border bg-bg px-2.5 py-1.5"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm">{ev.subject}</span>
                        {ongoing && (
                          <span className="shrink-0 rounded-full bg-emerald-500/15 px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-emerald-600 dark:text-emerald-400">
                            Now
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-muted">
                        {formatWhen(ev.start)}
                        {ev.end && ` – ${formatWhen(ev.end)}`}
                        {ev.attendees.length > 0 && (
                          <span className="ml-2 inline-flex items-center gap-0.5">
                            <Users size={10} /> {ev.attendees.length}
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => void createFromMeeting(ev)}
                      disabled={busy || startingFrom !== null}
                      className="inline-flex shrink-0 items-center gap-1 rounded bg-accent px-2 py-1 text-[12px] text-white disabled:opacity-50"
                    >
                      {startingFrom === key ? (
                        <Loader2 size={11} className="animate-spin" />
                      ) : (
                        <Radio size={11} />
                      )}
                      Start
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            !agendaLoading && (
              <p className="text-[12px] text-muted">
                {agendaDetail ?? "No current or upcoming meetings on your calendar."}
              </p>
            )
          )}
        </section>
      )}

      <div className="flex items-center gap-3 text-[11px] uppercase tracking-wide text-muted">
        <span className="h-px flex-1 bg-border" />
        or start manually
        <span className="h-px flex-1 bg-border" />
      </div>

      <div className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-[12px] text-muted">
          Title <span className="opacity-70">(optional)</span>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Sprint planning — payments"
            className="rounded border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none focus:border-accent"
          />
        </label>

        <label className="flex flex-col items-start gap-1 text-[12px] text-muted">
          Attach a topic for context <span className="opacity-70">(optional)</span>
          <TopicPicker topics={allTopics} value={topicId} onChange={setTopicId} />
        </label>

        <label className="flex flex-col items-start gap-1 text-[12px] text-muted">
          Meeting language
          <Select
            value={language}
            onChange={setLanguage}
            options={languageOptions}
            ariaLabel="Meeting language"
            fullWidth
          />
        </label>
      </div>

      {error && <div className="text-[12px] text-red-500">{error}</div>}

      <div>
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white disabled:opacity-60"
        >
          <Radio size={14} /> {busy ? "Creating…" : "Start session"}
        </button>
      </div>
    </div>
  );
}
