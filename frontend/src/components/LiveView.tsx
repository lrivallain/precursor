import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookmarkPlus,
  Check,
  CircleHelp,
  Loader2,
  Mic,
  Radio,
  RefreshCw,
  Send,
  Square,
  Trash2,
} from "lucide-react";
import type {
  MeetingInsight,
  MeetingInsightKind,
  MeetingSegment,
  MeetingSession,
  TopicNode,
} from "../lib/types";
import { api } from "../lib/api";
import { streamMeetingAsk } from "../lib/sse";
import { useSettings } from "../lib/settingsStore";
import { useResizableHeight } from "../lib/useResizableHeight";
import {
  listAudioInputDevices,
  useConversationTranscriber,
  type AudioInputDevice,
} from "../lib/useConversationTranscriber";
import { useConfirm } from "./ConfirmDialog";
import { LiveAudioHelp } from "./LiveAudioHelp";
import { TopicPicker } from "./TopicPicker";
import { DevicePicker } from "./DevicePicker";
import { Select } from "./Select";
import { LivePanel, type LiveTab } from "./LivePanel";
import { SummarySection } from "./SummarySection";
import { NotesSection } from "./NotesSection";
import { ContextSection } from "./ContextSection";
import { SpeakerNamePicker } from "./SpeakerNamePicker";
import { Markdown } from "./Markdown";

const LANGUAGES: { value: string; label: string }[] = [
  { value: "", label: "Default" },
  { value: "en-US", label: "English (US)" },
  { value: "en-GB", label: "English (UK)" },
  { value: "fr-FR", label: "French" },
  { value: "de-DE", label: "German" },
  { value: "es-ES", label: "Spanish" },
  { value: "it-IT", label: "Italian" },
  { value: "pt-PT", label: "Portuguese" },
  { value: "nl-NL", label: "Dutch" },
];

// Display order + labels for insight groups.
const KIND_META: { kind: MeetingInsightKind; label: string; dot: string }[] = [
  { kind: "action_item", label: "Action items", dot: "bg-sky-500" },
  { kind: "decision", label: "Decisions", dot: "bg-emerald-500" },
  { kind: "question", label: "Open questions", dot: "bg-amber-500" },
  { kind: "suggestion", label: "Suggestions", dot: "bg-violet-500" },
  { kind: "risk", label: "Risks", dot: "bg-rose-500" },
  { kind: "note", label: "Notes", dot: "bg-slate-400" },
];

const SPEAKER_COLORS = [
  "text-sky-600 dark:text-sky-400",
  "text-violet-600 dark:text-violet-400",
  "text-emerald-600 dark:text-emerald-400",
  "text-amber-600 dark:text-amber-400",
  "text-rose-600 dark:text-rose-400",
  "text-teal-600 dark:text-teal-400",
];

function speakerColor(label: string | null): string {
  if (!label) return "text-muted";
  let h = 0;
  for (let i = 0; i < label.length; i++) h = (h * 31 + label.charCodeAt(i)) >>> 0;
  return SPEAKER_COLORS[h % SPEAKER_COLORS.length];
}

// Diarization labels are stored as "<run>:<label>" (e.g. "2:Guest-1"): Azure
// re-numbers speakers on every stop/restart, so the run prefix scopes a rename
// to its own recording run and prevents names bleeding onto a different voice.
function stripRun(label: string): string {
  return label.replace(/^\d+:/, "");
}

function labelRun(label: string | null): number {
  const m = label ? /^(\d+):/.exec(label) : null;
  return m ? Number(m[1]) : 0;
}

function formatOffset(ms: number | null): string {
  if (ms == null) return "";
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

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

// Trigger analysis this long after speech pauses, and at least this often while
// someone is talking continuously.
const SILENCE_MS = 7000;
const MAX_INTERVAL_MS = 25000;

interface LiveViewProps {
  session: MeetingSession;
  topics: TopicNode[];
  onUpdated: (session: MeetingSession) => void;
  onDeleted: () => void | Promise<void>;
  /** Report the recording session id (or null) so the sidebar can show a dot. */
  onRecordingChange?: (sessionId: number | null) => void;
}

/**
 * Live meeting session view. A toolbar drives capture; the content is a tabbed,
 * splittable panel with four sections — Transcript, Live Insights (+ Q&A),
 * Summary, and Context — so the user can view two at once side by side.
 */
export function LiveView({ session, topics, onUpdated, onDeleted, onRecordingChange }: LiveViewProps) {
  const confirmAction = useConfirm();
  const settings = useSettings();
  const sttReady = settings?.stt_azure_ready ?? false;

  const [busy, setBusy] = useState(false);
  const [segments, setSegments] = useState<MeetingSegment[]>([]);
  const [interim, setInterim] = useState("");
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState<string>("");
  const [captureMic, setCaptureMic] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const [summaryText, setSummaryText] = useState("");
  const [summaryGenerating, setSummaryGenerating] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [summaryFocus, setSummaryFocus] = useState(0);
  const genRef = useRef(false);

  // Live notes (Markdown). Owned here so switching tabs never drops in-progress
  // content; autosaved (debounced) and flushed when the session is ended.
  const [notes, setNotes] = useState(session.notes ?? "");
  const [notesSaving, setNotesSaving] = useState(false);
  const [notesSaved, setNotesSaved] = useState(false);
  const savedNotesRef = useRef(session.notes ?? "");
  const notesRef = useRef(notes);
  notesRef.current = notes;

  // Topic-context summary (Context tab). Auto-generated when a topic is linked.
  const [topicSummary, setTopicSummary] = useState("");
  const [topicSummaryLoading, setTopicSummaryLoading] = useState(false);
  const [topicSummaryError, setTopicSummaryError] = useState<string | null>(null);
  const topicGenRef = useRef(false);
  const summarizedTopicRef = useRef<number | null>(null);

  // Raw diarization label currently being renamed inline, keyed by segment id so
  // only the clicked occurrence shows the editor (the rename applies to all).
  const [editingSegId, setEditingSegId] = useState<number | null>(null);

  // Segment indices where recording resumed (draw a separator before them).
  const [recordingBoundaries, setRecordingBoundaries] = useState<number[]>([]);
  const prevListeningRef = useRef(false);
  // Current recording-run ordinal. Diarization labels are namespaced with it so
  // renames stay scoped to the run they were made in (Azure re-numbers speakers
  // on each stop/restart). Seeded from the loaded transcript, bumped on start.
  const runRef = useRef(0);

  const [insights, setInsights] = useState<MeetingInsight[]>([]);
  const [analyzing, setAnalyzing] = useState(false);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);
  // The question currently being answered (echoed above the answer).
  const [askedQuestion, setAskedQuestion] = useState("");
  const [noteAdded, setNoteAdded] = useState(false);
  // Ask-assistant input height, resized from a top handle like the app composer.
  const { height: askHeight, onMouseDown: onAskResize } = useResizableHeight({
    storageKey: "precursor:live-ask-height",
    defaultHeight: 56,
    min: 36,
    max: 320,
    side: "top",
  });

  const transcriptRef = useRef<HTMLDivElement>(null);
  const restartRef = useRef(false);
  const analyzingRef = useRef(false);
  const lastAnalyzedRef = useRef(0);
  const segCountRef = useRef(0);
  segCountRef.current = segments.length;

  const isEnded = session.status === "ended";

  const allTopics = useMemo(() => flattenTopicNodes(topics), [topics]);
  const topicTitle = useMemo(
    () => allTopics.find((t) => t.id === session.topic_id)?.title ?? null,
    [allTopics, session.topic_id],
  );

  const handleFinalSegment = useCallback(
    (seg: { text: string; speakerLabel: string | null; offsetMs: number }) => {
      // Scope the raw diarization label to the current recording run.
      const scopedLabel = seg.speakerLabel ? `${runRef.current}:${seg.speakerLabel}` : null;
      void (async () => {
        try {
          const saved = await api.appendMeetingSegment(session.id, {
            text: seg.text,
            speaker_label: scopedLabel,
            offset_ms: seg.offsetMs,
          });
          setSegments((prev) => [...prev, saved]);
        } catch {
          setSegments((prev) => [
            ...prev,
            {
              id: -Date.now(),
              session_id: session.id,
              speaker_label: scopedLabel,
              text: seg.text,
              offset_ms: seg.offsetMs,
              created_at: new Date().toISOString(),
            },
          ]);
        }
      })();
    },
    [session.id],
  );

  const transcriber = useConversationTranscriber({
    onFinalSegment: handleFinalSegment,
    onInterim: setInterim,
    enabled: sttReady,
    lang: session.language || undefined,
    deviceId: deviceId || undefined,
    captureMic,
  });
  const recording = transcriber.listening;

  // Report recording state to the sidebar (red dot) + clear on unmount.
  useEffect(() => {
    onRecordingChange?.(recording ? session.id : null);
  }, [recording, session.id, onRecordingChange]);
  useEffect(() => () => onRecordingChange?.(null), [onRecordingChange]);

  // When a new recording run starts, bump the run ordinal so its diarization
  // labels are namespaced to this run; draw a boundary separator on a resume.
  useEffect(() => {
    if (recording && !prevListeningRef.current) {
      runRef.current += 1;
      if (segCountRef.current > 0) {
        setRecordingBoundaries((b) => [...b, segCountRef.current]);
      }
    }
    prevListeningRef.current = recording;
  }, [recording]);

  const runAnalysis = useCallback(async (): Promise<void> => {
    if (analyzingRef.current) return;
    analyzingRef.current = true;
    setAnalyzing(true);
    lastAnalyzedRef.current = segCountRef.current;
    try {
      const rows = await api.analyzeMeeting(session.id);
      setInsights(rows);
    } catch {
      // Non-fatal — keep the prior snapshot.
    } finally {
      analyzingRef.current = false;
      setAnalyzing(false);
    }
  }, [session.id]);

  // Load the existing transcript + insights when opening a session.
  useEffect(() => {
    let cancelled = false;
    setSegments([]);
    setInterim("");
    setInsights([]);
    setAnswer("");
    setSummaryText("");
    setSummaryError(null);
    setRecordingBoundaries([]);
    prevListeningRef.current = false;
    runRef.current = 0;
    lastAnalyzedRef.current = 0;
    void api
      .listMeetingSegments(session.id)
      .then((rows) => {
        if (!cancelled) {
          setSegments(rows);
          lastAnalyzedRef.current = rows.length;
          // Continue run numbering above any run already present in the
          // stored transcript so a restart never reuses a prior run's labels.
          runRef.current = rows.reduce((m, r) => Math.max(m, labelRun(r.speaker_label)), 0);
        }
      })
      .catch(() => {});
    void api
      .listMeetingInsights(session.id)
      .then((rows) => {
        if (!cancelled) setInsights(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [session.id]);

  // Enumerate input devices once STT is configured (labels need permission).
  useEffect(() => {
    if (!sttReady) return;
    void listAudioInputDevices()
      .then(setDevices)
      .catch(() => {});
  }, [sttReady]);

  // Auto-scroll the transcript as phrases arrive.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [segments, interim]);

  // Analyze shortly after speech pauses (resets on each new phrase).
  useEffect(() => {
    if (!recording) return;
    if (segments.length <= lastAnalyzedRef.current) return;
    const t = setTimeout(() => void runAnalysis(), SILENCE_MS);
    return () => clearTimeout(t);
  }, [segments, recording, runAnalysis]);

  // Safety net: analyze at least periodically during continuous talking.
  useEffect(() => {
    if (!recording) return;
    const iv = setInterval(() => {
      if (segCountRef.current > lastAnalyzedRef.current) void runAnalysis();
    }, MAX_INTERVAL_MS);
    return () => clearInterval(iv);
  }, [recording, runAnalysis]);

  // Language changed mid-recording → cycle the recognizer once torn down.
  useEffect(() => {
    if (!transcriber.listening && restartRef.current) {
      restartRef.current = false;
      transcriber.start();
    }
  }, [transcriber.listening, transcriber]);

  async function applyLanguage(value: string): Promise<void> {
    const language = value || null;
    const updated = await api.updateMeetingSession(session.id, { language });
    onUpdated(updated);
    if (transcriber.listening) {
      restartRef.current = true;
      transcriber.stop();
    }
  }

  async function generateTopicSummary(): Promise<void> {
    if (topicGenRef.current || session.topic_id == null) return;
    topicGenRef.current = true;
    setTopicSummaryLoading(true);
    setTopicSummaryError(null);
    try {
      const res = await api.topicContextSummary(session.id);
      setTopicSummary(res.summary);
    } catch (e) {
      setTopicSummaryError(
        e instanceof Error ? e.message : "Couldn't summarize the topic.",
      );
    } finally {
      topicGenRef.current = false;
      setTopicSummaryLoading(false);
    }
  }

  // Always (re)summarize the attached topic when it changes — on link, or when
  // opening a session that already has a topic.
  useEffect(() => {
    if (session.topic_id == null) {
      summarizedTopicRef.current = null;
      setTopicSummary("");
      setTopicSummaryError(null);
      return;
    }
    if (summarizedTopicRef.current === session.topic_id) return;
    summarizedTopicRef.current = session.topic_id;
    setTopicSummary("");
    void generateTopicSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id, session.topic_id]);

  async function applyTopic(topicId: number | null): Promise<void> {
    const updated = await api.updateMeetingSession(session.id, { topic_id: topicId });
    onUpdated(updated);
  }

  async function generateSummary(): Promise<void> {
    setSummaryFocus((n) => n + 1);
    if (genRef.current) return;
    genRef.current = true;
    setSummaryGenerating(true);
    setSummaryError(null);
    try {
      const res = await api.summarizeMeeting(session.id);
      setSummaryText(res.summary);
    } catch (e) {
      setSummaryError(
        e instanceof Error ? e.message : "Couldn't generate a summary — record more first.",
      );
    } finally {
      genRef.current = false;
      setSummaryGenerating(false);
    }
  }

  async function setStatus(next: "active" | "ended"): Promise<void> {
    if (busy) return;
    if (next === "ended" && transcriber.listening) transcriber.stop();
    setBusy(true);
    try {
      // Persist any in-progress notes together with the status change on end.
      const payload =
        next === "ended" && notesRef.current !== savedNotesRef.current
          ? { status: next, notes: notesRef.current }
          : { status: next };
      const updated = await api.updateMeetingSession(session.id, payload);
      savedNotesRef.current = notesRef.current;
      onUpdated(updated);
      // Auto-draft a summary when the meeting ends (if anything was recorded).
      if (next === "ended" && segCountRef.current > 0) void generateSummary();
    } finally {
      setBusy(false);
    }
  }

  // Debounced autosave of live notes while the user types.
  useEffect(() => {
    if (notes === savedNotesRef.current) return;
    setNotesSaved(false);
    const t = setTimeout(() => {
      setNotesSaving(true);
      void api
        .updateMeetingSession(session.id, { notes })
        .then((updated) => {
          savedNotesRef.current = notes;
          onUpdated(updated);
          setNotesSaved(true);
        })
        .catch(() => {
          /* non-fatal — will retry on the next edit or on end */
        })
        .finally(() => setNotesSaving(false));
    }, 1200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notes, session.id]);

  async function remove(): Promise<void> {
    const ok = await confirmAction({
      variant: "danger",
      title: "Delete session",
      message: `Delete “${session.title}” and its transcript? This can't be undone.`,
      confirmLabel: "Delete",
    });
    if (!ok) return;
    if (transcriber.listening) transcriber.stop();
    setBusy(true);
    try {
      await api.deleteMeetingSession(session.id);
      await onDeleted();
    } finally {
      setBusy(false);
    }
  }

  async function ask(): Promise<void> {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setAnswer("");
    setAskedQuestion(q);
    setQuestion("");
    setNoteAdded(false);
    try {
      await streamMeetingAsk(session.id, q, {
        onEvent: (e) => {
          if (e.event === "token") {
            try {
              const { content } = JSON.parse(e.data) as { content?: string };
              if (content) setAnswer((a) => a + content);
            } catch {
              /* ignore malformed frame */
            }
          } else if (e.event === "error") {
            setAnswer("Sorry — the assistant couldn't answer that.");
          }
        },
      });
    } catch {
      setAnswer("Sorry — the assistant couldn't answer that.");
    } finally {
      setAsking(false);
    }
  }

  async function addAnswerToContext(): Promise<void> {
    const note = answer.trim();
    if (!note) return;
    try {
      const updated = await api.addMeetingContextNote(session.id, note);
      onUpdated(updated);
      setNoteAdded(true);
      setTimeout(() => setNoteAdded(false), 2000);
    } catch {
      /* non-fatal */
    }
  }

  const speakerNames = session.speaker_names ?? {};
  const displayName = (raw: string | null): string | null =>
    raw ? (speakerNames[raw] ?? stripRun(raw)) : null;

  function beginRename(seg: MeetingSegment): void {
    if (!seg.speaker_label) return;
    setEditingSegId(seg.id);
  }

  async function commitRename(rawLabel: string, value: string): Promise<void> {
    setEditingSegId(null);
    try {
      const updated = await api.renameMeetingSpeaker(session.id, rawLabel, value.trim());
      onUpdated(updated);
    } catch {
      // Non-fatal — leave the previous name in place.
    }
  }

  const groupedInsights = useMemo(
    () =>
      KIND_META.map((meta) => ({
        ...meta,
        items: insights.filter((i) => i.kind === meta.kind),
      })).filter((g) => g.items.length > 0),
    [insights],
  );

  // Attendee suggestions for the Summary: meeting invitees (and any transcript
  // display names) not already confirmed in the list. Confirmed speakers are
  // auto-added when named, so they don't reappear here.
  const suggestedAttendees = useMemo(() => {
    const seen = new Set<string>();
    const existing = new Set(session.attendees ?? []);
    const out: string[] = [];
    const push = (name: string | null | undefined) => {
      const n = (name ?? "").trim();
      if (n && !seen.has(n) && !existing.has(n)) {
        seen.add(n);
        out.push(n);
      }
    };
    for (const a of session.external_meeting?.attendees ?? []) push(a.name || a.email);
    for (const seg of segments) push(displayName(seg.speaker_label));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segments, session.attendees, session.speaker_names, session.external_meeting]);

  // Names offered as autocomplete when renaming a transcript speaker: the
  // linked meeting's invitees plus any attendees already on the summary.
  const speakerNameOptions = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    const push = (name: string | null | undefined) => {
      const n = (name ?? "").trim();
      if (n && !seen.has(n)) {
        seen.add(n);
        out.push(n);
      }
    };
    for (const a of session.external_meeting?.attendees ?? []) push(a.name || a.email);
    for (const n of session.attendees ?? []) push(n);
    return out;
  }, [session.external_meeting, session.attendees]);

  // ---- Section nodes -----------------------------------------------------
  const boundarySet = useMemo(() => new Set(recordingBoundaries), [recordingBoundaries]);
  const transcriptNode = (
    <div ref={transcriptRef} className="h-full overflow-y-auto px-4 py-4">
      {segments.length === 0 && !interim ? (
        <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
          <Radio size={20} className="mb-2 opacity-70" aria-hidden="true" />
          <p className="mb-1 font-medium text-text">No transcript yet</p>
          <p className="max-w-sm">
            Pick the input carrying your meeting audio, then press Record. Each
            phrase is transcribed with a speaker label and saved as you go.
          </p>
        </div>
      ) : (
        <div className="mx-auto max-w-3xl space-y-2">
          {segments.map((seg, i) => (
            <div key={seg.id}>
              {boundarySet.has(i) && (
                <div className="my-3 flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
                  <div className="h-px flex-1 bg-border" />
                  Recording resumed
                  <div className="h-px flex-1 bg-border" />
                </div>
              )}
              <div className="flex gap-2 text-sm">
                <span className="w-10 shrink-0 pt-0.5 text-right text-[11px] tabular-nums text-muted">
                  {formatOffset(seg.offset_ms)}
                </span>
                <div className="min-w-0 flex-1">
                  {seg.speaker_label &&
                    (editingSegId === seg.id ? (
                      <SpeakerNamePicker
                        value={displayName(seg.speaker_label) ?? ""}
                        options={speakerNameOptions}
                        color={speakerColor(seg.speaker_label)}
                        onCommit={(name) =>
                          void commitRename(seg.speaker_label as string, name)
                        }
                        onCancel={() => setEditingSegId(null)}
                      />
                    ) : (
                      <button
                        type="button"
                        onClick={() => beginRename(seg)}
                        data-tooltip="Rename speaker"
                        className={`mr-1.5 rounded text-[12px] font-medium hover:underline ${speakerColor(
                          seg.speaker_label,
                        )}`}
                      >
                        {displayName(seg.speaker_label)}
                      </button>
                    ))}
                  <span className="text-text">{seg.text}</span>
                </div>
              </div>
            </div>
          ))}
          {interim && (
            <div className="flex gap-2 text-sm">
              <span className="w-10 shrink-0" />
              <span className="min-w-0 flex-1 italic text-muted">{interim}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );

  const insightsNode = (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-[12px] font-medium">Live insights</span>
        <button
          type="button"
          onClick={() => void runAnalysis()}
          disabled={analyzing || segments.length === 0}
          data-tooltip="Analyze now"
          aria-label="Analyze now"
          className="rounded p-1 text-muted hover:bg-surface hover:text-accent disabled:opacity-40"
        >
          <RefreshCw size={14} className={analyzing ? "animate-spin" : ""} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        {groupedInsights.length === 0 ? (
          <p className="text-[12px] text-muted">
            {segments.length === 0
              ? "Insights appear here once the meeting gets going."
              : "No insights yet — analysis runs as the discussion pauses."}
          </p>
        ) : (
          <div className="space-y-3">
            {groupedInsights.map((g) => (
              <div key={g.kind}>
                <div className="mb-1 flex items-center gap-1.5">
                  <span className={`h-1.5 w-1.5 rounded-full ${g.dot}`} />
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted">
                    {g.label}
                  </span>
                </div>
                <ul className="space-y-1">
                  {g.items.map((it) => (
                    <li key={it.id} className="text-[13px] leading-snug text-text">
                      {it.content}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="border-t border-border p-2">
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted">
          Ask assistant
        </div>
        {(askedQuestion || asking || answer) && (
          <div className="mb-2 max-h-56 space-y-1.5 overflow-y-auto rounded bg-surface px-2 py-2">
            {askedQuestion && (
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-lg bg-accent/15 px-2.5 py-1.5 text-[13px] text-text">
                  {askedQuestion}
                </div>
              </div>
            )}
            {asking && !answer ? (
              <div className="flex items-center gap-2 text-[12px] text-muted">
                <Loader2 size={13} className="animate-spin" /> Thinking…
              </div>
            ) : answer ? (
              <div className="prose prose-sm dark:prose-invert max-w-none text-[13px]">
                <Markdown>{answer}</Markdown>
              </div>
            ) : null}
            {answer && !asking && (
              <div className="flex items-center gap-2 pt-0.5">
                <button
                  type="button"
                  onClick={() => void addAnswerToContext()}
                  disabled={noteAdded}
                  data-tooltip="Pin this answer so it grounds future insights, Q&A and the summary"
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] hover:bg-bg disabled:opacity-60"
                >
                  {noteAdded ? <Check size={12} /> : <BookmarkPlus size={12} />}
                  {noteAdded ? "Added to context" : "Add to context"}
                </button>
              </div>
            )}
          </div>
        )}
        <div className="relative flex items-end gap-1.5">
          <div
            role="separator"
            aria-orientation="horizontal"
            onMouseDown={onAskResize}
            title="Drag to resize"
            className="group absolute -top-2 left-0 right-0 z-10 h-2 cursor-row-resize"
          >
            <div className="mx-auto mt-1 h-px w-12 bg-border transition-colors group-hover:bg-accent/60" />
          </div>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask();
              }
            }}
            style={{ height: askHeight }}
            placeholder="Ask about anything discussed…"
            className="min-w-0 flex-1 resize-none rounded border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none focus:border-accent"
          />
          <button
            type="button"
            onClick={() => void ask()}
            disabled={asking || !question.trim()}
            aria-label="Ask"
            className="rounded bg-accent p-2 text-white disabled:opacity-50"
          >
            <Send size={15} />
          </button>
        </div>
      </div>
    </div>
  );

  const summaryNode = (
    <SummarySection
      session={session}
      onUpdated={onUpdated}
      text={summaryText}
      setText={setSummaryText}
      generating={summaryGenerating}
      error={summaryError}
      onGenerate={() => void generateSummary()}
      suggestedAttendees={suggestedAttendees}
      topicTitle={topicTitle}
      canGenerate={segments.length > 0}
    />
  );

  const notesNode = (
    <NotesSection
      text={notes}
      setText={setNotes}
      saving={notesSaving}
      saved={notesSaved}
      onUpload={(file) => api.uploadMeetingAttachment(session.id, file)}
    />
  );

  const contextNode = (
    <ContextSection
      session={session}
      onUpdated={onUpdated}
      topicTitle={topicTitle}
      topicSummary={topicSummary}
      topicSummaryLoading={topicSummaryLoading}
      topicSummaryError={topicSummaryError}
      onRefreshTopicSummary={() => void generateTopicSummary()}
    />
  );

  const hasSummary = summaryText.trim().length > 0 || summaryGenerating;
  // The summary only exists for an ended session — hide the whole tab until then.
  const tabs: LiveTab[] = [
    { id: "transcript", label: "Transcript" },
    { id: "insights", label: "Live insights", badge: insights.length },
    { id: "notes", label: notes.trim() ? "Notes ●" : "Notes" },
    ...(isEnded ? [{ id: "summary", label: hasSummary ? "Summary ●" : "Summary" }] : []),
    { id: "context", label: "Context" },
  ];

  function renderSection(id: string): React.ReactNode {
    switch (id) {
      case "transcript":
        return transcriptNode;
      case "insights":
        return insightsNode;
      case "notes":
        return notesNode;
      case "summary":
        return summaryNode;
      case "context":
        return contextNode;
      default:
        return null;
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2">
        {recording ? (
          <button
            type="button"
            onClick={() => transcriber.stop()}
            className="inline-flex items-center gap-1.5 rounded bg-red-600 px-2.5 py-1.5 text-sm text-white hover:bg-red-500"
          >
            <Square size={14} /> Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={() => transcriber.start()}
            disabled={!sttReady || isEnded}
            data-tooltip={
              !sttReady
                ? "Configure Azure Speech in Settings first"
                : isEnded
                  ? "Reopen the session to record"
                  : undefined
            }
            className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1.5 text-sm text-white disabled:opacity-50"
          >
            <Mic size={14} /> Record
          </button>
        )}

        {recording && (
          <span className="inline-flex items-center gap-1 text-[12px] font-medium text-red-500">
            <span className="h-2 w-2 rounded-full bg-red-500" />
            Recording
          </span>
        )}

        <div className="flex items-center gap-1">
          <DevicePicker
            devices={devices}
            value={deviceId}
            onChange={setDeviceId}
            disabled={recording || !sttReady}
          />
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            aria-label="How to capture meeting audio"
            data-tooltip="How to capture meeting audio"
            className="rounded p-1 text-muted hover:bg-surface hover:text-accent"
          >
            <CircleHelp size={16} />
          </button>
        </div>

        <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
          <input
            type="checkbox"
            checked={captureMic}
            onChange={(e) => setCaptureMic(e.target.checked)}
            disabled={recording || !deviceId}
            className="accent-accent"
          />
          + mic
        </label>

        <Select
          value={session.language ?? ""}
          onChange={(v) => void applyLanguage(v)}
          options={LANGUAGES}
          ariaLabel="Meeting language"
          size="sm"
        />

        <label className="inline-flex items-center gap-1 text-[11px] text-muted">
          Topic
          <TopicPicker
            topics={allTopics}
            value={session.topic_id}
            onChange={(id) => void applyTopic(id)}
          />
        </label>

        <div className="ml-auto flex items-center gap-2">
          {isEnded ? (
            <button
              type="button"
              onClick={() => void setStatus("active")}
              disabled={busy}
              className="rounded border border-border px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-60"
            >
              Reopen
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void setStatus("ended")}
              disabled={busy}
              className="rounded border border-border px-2.5 py-1.5 text-sm hover:bg-surface disabled:opacity-60"
            >
              End session
            </button>
          )}
          <button
            type="button"
            onClick={() => void remove()}
            disabled={busy}
            aria-label="Delete session"
            data-tooltip="Delete session"
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-red-500 disabled:opacity-60"
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>

      {transcriber.error && (
        <div className="border-b border-border bg-red-500/10 px-4 py-1.5 text-[12px] text-red-500">
          {transcriber.error}
        </div>
      )}
      {!sttReady && (
        <div className="border-b border-border bg-amber-500/10 px-4 py-1.5 text-[12px] text-amber-700 dark:text-amber-300">
          Azure Speech isn&apos;t configured — set a Speech key + endpoint in
          Settings to enable transcription.
        </div>
      )}

      <LivePanel
        tabs={tabs}
        render={renderSection}
        storageKey="precursor:live-panel"
        focus={{ id: "summary", nonce: summaryFocus }}
      />

      {helpOpen && <LiveAudioHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
