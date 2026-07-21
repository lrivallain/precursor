import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  CircleHelp,
  Lightbulb,
  Loader2,
  Mic,
  Radio,
  RefreshCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import type {
  Chat,
  MeetingInsight,
  MeetingInsightKind,
  MeetingSegment,
  MeetingSession,
  TopicNode,
} from "../lib/types";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
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
import { LiveChatSection } from "./LiveChatSection";
import { TranslationSection } from "./TranslationSection";
import { FeaturePicker, type FeatureOption } from "./FeaturePicker";
import { SpeakerNamePicker } from "./SpeakerNamePicker";
import { Markdown } from "./Markdown";
import { HighlightedText } from "../lib/searchHighlight";

// Persisted audio-capture preferences (input device + mic mix-in). Kept in
// localStorage so a user's choice carries across sessions and app restarts.
const AUDIO_DEVICE_KEY = "precursor.live.audioDeviceId";
const AUDIO_MIC_KEY = "precursor.live.captureMic";

function readStoredDeviceId(): string {
  try {
    return window.localStorage.getItem(AUDIO_DEVICE_KEY) ?? "";
  } catch {
    return "";
  }
}

function readStoredCaptureMic(): boolean {
  try {
    return window.localStorage.getItem(AUDIO_MIC_KEY) === "1";
  } catch {
    return false;
  }
}

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

const FEATURE_OPTIONS: FeatureOption[] = [
  {
    id: "insights",
    label: "Live insights & assist",
    description: "Action items, decisions, risks + proactive help",
  },
  { id: "notes", label: "Notes", description: "Your Markdown scratch pad" },
  { id: "assistant", label: "Assistant", description: "A grounded chat about the meeting" },
  { id: "translation", label: "Translation", description: "Live transcript translation" },
];

const SPEAKER_COLORS = [
  "text-sky-600 dark:text-sky-400",
  "text-violet-600 dark:text-violet-400",
  "text-emerald-600 dark:text-emerald-400",
  "text-amber-600 dark:text-amber-400",
  "text-rose-600 dark:text-rose-400",
  "text-teal-600 dark:text-teal-400",
];

// Live translation runs continuously in the background (even when its tab isn't
// visible); only the newest lines are translated when it (re)starts.
const TRANSLATE_DEBOUNCE_MS = 1200;
const TRANSLATE_WINDOW = 6;

interface Suggestion {
  id: number;
  text: string;
  at: number;
}

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
  /** Archive the session (hide from the list, keep restorable). */
  onArchived?: () => void | Promise<void>;
  /** Report the recording session id (or null) so the sidebar can show a dot. */
  onRecordingChange?: (sessionId: number | null) => void;
}

/**
 * Live meeting session view. A toolbar drives capture; the content is a tabbed,
 * splittable panel with four sections — Transcript, Live Insights (+ Q&A),
 * Summary, and Context — so the user can view two at once side by side.
 */
export function LiveView({
  session,
  topics,
  onUpdated,
  onDeleted,
  onArchived,
  onRecordingChange,
}: LiveViewProps) {
  const confirmAction = useConfirm();
  const settings = useSettings();
  const sttReady = settings?.stt_azure_ready ?? false;

  const [busy, setBusy] = useState(false);
  const [segments, setSegments] = useState<MeetingSegment[]>([]);
  const [interim, setInterim] = useState("");
  const [devices, setDevices] = useState<AudioInputDevice[]>([]);
  const [deviceId, setDeviceId] = useState<string>(readStoredDeviceId);
  const [captureMic, setCaptureMic] = useState<boolean>(readStoredCaptureMic);
  const [helpOpen, setHelpOpen] = useState(false);

  const [summaryText, setSummaryText] = useState(session.summary ?? "");
  const [summaryGenerating, setSummaryGenerating] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  // Scraping + summarizing the linked Teams meeting transcript (WorkIQ path).
  const [transcriptScraping, setTranscriptScraping] = useState(false);
  // Ask the panel to surface a tab (bump the nonce). Used after generating the
  // summary and after starting the assistant.
  const [panelFocus, setPanelFocus] = useState<{ id: string; nonce: number }>({
    id: "",
    nonce: 0,
  });
  const focusTab = (id: string) => setPanelFocus((f) => ({ id, nonce: f.nonce + 1 }));
  const genRef = useRef(false);

  // Live notes (Markdown). Owned here so switching tabs never drops in-progress
  // content; autosaved (debounced) and flushed when the session is ended.
  const [notes, setNotes] = useState(session.notes ?? "");
  const [notesSaving, setNotesSaving] = useState(false);
  const [notesSaved, setNotesSaved] = useState(false);
  const savedNotesRef = useRef(session.notes ?? "");
  const notesRef = useRef(notes);
  notesRef.current = notes;

  // The chat spawned for the assistant tab (created on first ask). Held here so
  // it survives tab switches; loaded from the session's chat_id when present.
  const [chat, setChat] = useState<Chat | null>(null);
  useEffect(() => {
    if (session.chat_id == null) {
      setChat(null);
      return;
    }
    let cancelled = false;
    void api.chats.get(session.chat_id)
      .then((c) => {
        if (!cancelled) setChat(c);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [session.id, session.chat_id]);
  const [startingAssistant, setStartingAssistant] = useState(false);

  // Spawn + attach the assistant chat. Triggered when the "assistant" feature is
  // enabled — we never create a chat implicitly.
  async function startAssistant(): Promise<void> {
    if (chat || startingAssistant) return;
    setStartingAssistant(true);
    try {
      const c = await api.meetings.ensureChat(session.id);
      setChat(c);
      focusTab("assistant");
    } catch {
      /* non-fatal — re-enabling the feature retries */
    } finally {
      setStartingAssistant(false);
    }
  }

  // Enable/disable optional Live features for this session. Enabling the
  // assistant spawns its chat; the others just reveal their tabs/processing.
  async function applyFeatures(next: string[]): Promise<void> {
    try {
      const updated = await api.meetings.setFeatures(session.id, next);
      onUpdated(updated);
      if (next.includes("assistant") && !chat) void startAssistant();
    } catch {
      /* non-fatal */
    }
  }

  // Topic-context summary (Context tab). Generated once when a topic is linked,
  // then persisted on the session — seeded from that cache on later opens so we
  // don't re-summarize on every display. Refreshed only on demand.
  const [topicSummary, setTopicSummary] = useState(session.topic_summary ?? "");
  const [topicSummaryLoading, setTopicSummaryLoading] = useState(false);
  const [topicSummaryError, setTopicSummaryError] = useState<string | null>(null);
  const topicGenRef = useRef(false);
  const summarizedTopicRef = useRef<number | null>(null);

  // Raw diarization label currently being renamed inline, keyed by segment id so
  // only the clicked occurrence shows the editor (the rename applies to all).
  const [editingSegId, setEditingSegId] = useState<number | null>(null);

  // Segment indices where recording (re)started — draw a separator before them.
  // `resume` = user pressed Record again; `reconnect` = a transparent recovery
  // from an Azure ~20-min drop, which also starts a new speaker namespace.
  const [recordingBoundaries, setRecordingBoundaries] = useState<
    { index: number; kind: "resume" | "reconnect" }[]
  >([]);
  const prevListeningRef = useRef(false);
  // Current recording-run ordinal. Diarization labels are namespaced with it so
  // renames stay scoped to the run they were made in (Azure re-numbers speakers
  // on each stop/restart). Seeded from the loaded transcript, bumped on start.
  const runRef = useRef(0);

  const [insights, setInsights] = useState<MeetingInsight[]>([]);
  const [analyzing, setAnalyzing] = useState(false);

  // Live translation state (loop runs in this always-mounted component so it
  // keeps translating even when the Translation tab isn't the active pane).
  const [translationLang, setTranslationLang] = useState(
    (session.language || "").split("-")[0] || "en",
  );
  const [translations, setTranslations] = useState<Record<number, string>>({});
  const [translateStart, setTranslateStart] = useState<number | null>(null);
  const [translating, setTranslating] = useState(false);
  const [translateError, setTranslateError] = useState<string | null>(null);
  const translateRunningRef = useRef(false);
  const translateNextRef = useRef(0);

  // Proactive suggestions now ride along on the unified analysis pass (see
  // runAnalysis). Surfaced as dismissible cards in the Insights tab.
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const assistLastTextRef = useRef("");

  const transcriptRef = useRef<HTMLDivElement>(null);
  const restartRef = useRef(false);
  const analyzingRef = useRef(false);
  const lastAnalyzedRef = useRef(0);
  const segCountRef = useRef(0);
  segCountRef.current = segments.length;

  const isEnded = session.status === "ended";
  const features = session.features ?? [];
  const insightsOn = features.includes("insights");
  const notesOn = features.includes("notes");
  const assistantOn = features.includes("assistant");
  const translationOn = features.includes("translation");

  const allTopics = useMemo(() => flattenTopicNodes(topics), [topics]);
  const linkedTopic = useMemo(
    () => allTopics.find((t) => t.id === session.topic_id) ?? null,
    [allTopics, session.topic_id],
  );
  const topicTitle = linkedTopic?.title ?? null;
  // Issue linked to the attached topic (if any); posting the recap also mirrors
  // it as a comment on this issue.
  const topicIssueNumber = linkedTopic?.github_issue_number ?? null;

  const handleFinalSegment = useCallback(
    (seg: { text: string; speakerLabel: string | null; offsetMs: number }) => {
      // Scope the raw diarization label to the current recording run.
      const scopedLabel = seg.speakerLabel ? `${runRef.current}:${seg.speakerLabel}` : null;
      void (async () => {
        try {
          const saved = await api.meetings.appendSegment(session.id, {
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

  // A transparent reconnect (Azure ~20-min 1006 drop, recovered without a hard
  // cut) re-numbers speakers, so start a fresh voice-attribution run and mark
  // the boundary as a reconnect.
  const handleReconnect = useCallback(() => {
    runRef.current += 1;
    if (segCountRef.current > 0) {
      setRecordingBoundaries((b) => [...b, { index: segCountRef.current, kind: "reconnect" }]);
    }
  }, []);

  const transcriber = useConversationTranscriber({
    onFinalSegment: handleFinalSegment,
    onInterim: setInterim,
    enabled: sttReady,
    lang: session.language || undefined,
    deviceId: deviceId || undefined,
    captureMic,
    onReconnect: handleReconnect,
  });
  const recording = transcriber.listening;
  const starting = transcriber.starting;

  // While recording, guard against a full page unload (reload, tab/window close,
  // or app quit) silently dropping the capture. The browser shows its native
  // "leave site?" prompt; in-app navigation is guarded separately in App.
  useEffect(() => {
    if (!recording) return;
    function onBeforeUnload(e: BeforeUnloadEvent): void {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [recording]);

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
        setRecordingBoundaries((b) => [...b, { index: segCountRef.current, kind: "resume" }]);
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
      const res = await api.meetings.analyze(session.id);
      setInsights(res.insights);
      // A proactive suggestion rides along on the same pass — surface genuinely
      // new ones as dismissible cards in the Insights tab.
      const text = (res.suggestion ?? "").trim();
      if (text && text !== assistLastTextRef.current) {
        assistLastTextRef.current = text;
        setSuggestions((prev) => [{ id: Date.now(), text, at: Date.now() }, ...prev].slice(0, 12));
      }
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
    // Seed the editable recap from the persisted summary (empty when none).
    // Must not clear it: this effect re-runs on every open/remount, so blanking
    // here would drop a stored summary when returning to the session.
    setSummaryText(session.summary ?? "");
    setSummaryError(null);
    setRecordingBoundaries([]);
    prevListeningRef.current = false;
    runRef.current = 0;
    lastAnalyzedRef.current = 0;
    void api.meetings.listSegments(session.id)
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
    void api.meetings.listInsights(session.id)
      .then((rows) => {
        if (!cancelled) setInsights(rows);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id]);

  // Enumerate input devices once STT is configured (labels need permission).
  useEffect(() => {
    if (!sttReady) return;
    void listAudioInputDevices()
      .then(setDevices)
      .catch(() => {});
  }, [sttReady]);

  // Persist the audio-capture prefs so they survive across sessions/restarts.
  useEffect(() => {
    try {
      window.localStorage.setItem(AUDIO_DEVICE_KEY, deviceId);
    } catch {
      /* storage unavailable — prefs just won't persist */
    }
  }, [deviceId]);
  useEffect(() => {
    try {
      window.localStorage.setItem(AUDIO_MIC_KEY, captureMic ? "1" : "0");
    } catch {
      /* storage unavailable — prefs just won't persist */
    }
  }, [captureMic]);

  // Auto-scroll the transcript as phrases arrive.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [segments, interim]);

  // Analyze shortly after speech pauses (resets on each new phrase).
  useEffect(() => {
    if (!recording || !insightsOn) return;
    if (segments.length <= lastAnalyzedRef.current) return;
    const t = setTimeout(() => void runAnalysis(), SILENCE_MS);
    return () => clearTimeout(t);
  }, [segments, recording, runAnalysis, insightsOn]);

  // Safety net: analyze at least periodically during continuous talking.
  useEffect(() => {
    if (!recording || !insightsOn) return;
    const iv = setInterval(() => {
      if (segCountRef.current > lastAnalyzedRef.current) void runAnalysis();
    }, MAX_INTERVAL_MS);
    return () => clearInterval(iv);
  }, [recording, runAnalysis, insightsOn]);

  // Language changed mid-recording → cycle the recognizer once torn down.
  useEffect(() => {
    if (!transcriber.listening && restartRef.current) {
      restartRef.current = false;
      transcriber.start();
    }
  }, [transcriber.listening, transcriber]);

  async function applyLanguage(value: string): Promise<void> {
    const language = value || null;
    const updated = await api.meetings.updateSession(session.id, { language });
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
      const res = await api.meetings.topicContextSummary(session.id);
      setTopicSummary(res.summary);
      // The backend persisted the summary; mirror it onto the session so the
      // cache stays in sync and we don't regenerate on the next open.
      onUpdated({ ...session, topic_summary: res.summary || null });
    } catch (e) {
      setTopicSummaryError(
        e instanceof Error ? e.message : "Couldn't summarize the topic.",
      );
    } finally {
      topicGenRef.current = false;
      setTopicSummaryLoading(false);
    }
  }

  // Seed the Context tab from the persisted summary when a topic is attached;
  // only generate one when none is cached yet (on first link). The user can
  // refresh on demand — we don't re-summarize on every open to save tokens.
  useEffect(() => {
    if (session.topic_id == null) {
      summarizedTopicRef.current = null;
      setTopicSummary("");
      setTopicSummaryError(null);
      return;
    }
    if (summarizedTopicRef.current === session.topic_id) return;
    summarizedTopicRef.current = session.topic_id;
    const cached = session.topic_summary ?? "";
    if (cached) {
      setTopicSummary(cached);
      return;
    }
    setTopicSummary("");
    void generateTopicSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.id, session.topic_id]);

  async function applyTopic(topicId: number | null): Promise<void> {
    const updated = await api.meetings.updateSession(session.id, { topic_id: topicId });
    onUpdated(updated);
  }

  async function generateSummary(base: MeetingSession = session): Promise<void> {
    focusTab("summary");
    if (genRef.current) return;
    genRef.current = true;
    setSummaryGenerating(true);
    setSummaryError(null);
    try {
      const res = await api.meetings.summarize(session.id);
      setSummaryText(res.summary);
      // The backend persisted the recap; mirror it onto the session so a later
      // open (or the "Summary ●" dot) stays in sync without regenerating. Merge
      // onto `base` (the caller's freshest session) rather than the closure's
      // `session` prop, which is stale when auto-drafting right after end.
      onUpdated({ ...base, summary: res.summary || null });
    } catch (e) {
      setSummaryError(
        e instanceof Error ? e.message : "Couldn't generate a summary — record more first.",
      );
    } finally {
      genRef.current = false;
      setSummaryGenerating(false);
    }
  }

  async function generateFromTranscript(): Promise<void> {
    focusTab("summary");
    if (genRef.current || transcriptScraping) return;
    setTranscriptScraping(true);
    setSummaryError(null);
    try {
      const res = await api.meetings.summarizeFromTranscript(session.id);
      setSummaryText(res.summary);
      onUpdated({ ...session, summary: res.summary || null });
    } catch (e) {
      setSummaryError(
        e instanceof Error
          ? e.message
          : "Couldn't summarize the Teams transcript — it may not be published yet.",
      );
    } finally {
      setTranscriptScraping(false);
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
      const updated = await api.meetings.updateSession(session.id, payload);
      savedNotesRef.current = notesRef.current;
      onUpdated(updated);
      // Auto-draft a summary when the meeting ends — but only if none exists
      // yet. Once generated (or drafted), it's never auto-regenerated: the user
      // drives any refresh from the Summary tab.
      if (
        next === "ended" &&
        segCountRef.current > 0 &&
        !session.summary &&
        !summaryText.trim()
      )
        void generateSummary(updated);
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
      void api.meetings.updateSession(session.id, { notes })
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
      await api.meetings.deleteSession(session.id);
      await onDeleted();
    } finally {
      setBusy(false);
    }
  }

  async function archive(): Promise<void> {
    if (!onArchived) return;
    const ok = await confirmAction({
      title: "Archive session",
      message: `Archive “${session.title}”? You can restore it later from the archive.`,
      confirmLabel: "Archive",
    });
    if (!ok) return;
    if (transcriber.listening) transcriber.stop();
    setBusy(true);
    try {
      await api.meetings.archiveSession(session.id);
      await onArchived();
    } finally {
      setBusy(false);
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
      const updated = await api.meetings.renameSpeaker(session.id, rawLabel, value.trim());
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

  // Transcript segments prepared for live translation — same layout + speaker
  // colors as the transcript.
  const translationItems = useMemo(
    () =>
      segments.map((seg) => ({
        id: seg.id,
        speaker: displayName(seg.speaker_label),
        colorClass: speakerColor(seg.speaker_label),
        time: formatOffset(seg.offset_ms),
        text: seg.text,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [segments, session.speaker_names],
  );
  const translationItemsRef = useRef(translationItems);
  translationItemsRef.current = translationItems;

  // Restart translation from a fresh window (language change / manual refresh).
  function restartTranslation(nextLang: string): void {
    setTranslationLang(nextLang);
    const s = Math.max(0, translationItemsRef.current.length - TRANSLATE_WINDOW);
    translateNextRef.current = s;
    setTranslateStart(s);
    setTranslations({});
    setTranslateError(null);
  }

  const runTranslation = useCallback(async (): Promise<void> => {
    if (translateRunningRef.current) return;
    const items = translationItemsRef.current;
    const start = translateNextRef.current;
    if (items.length <= start) return;
    const batch = items.slice(start);
    translateRunningRef.current = true;
    setTranslating(true);
    setTranslateError(null);
    try {
      const res = await api.meetings.translate(
        session.id,
        translationLang,
        batch.map((b) => b.text),
      );
      const lines = res.lines ?? [];
      setTranslations((prev) => {
        const next = { ...prev };
        batch.forEach((b, i) => {
          next[b.id] = lines[i] ?? b.text;
        });
        return next;
      });
      translateNextRef.current = start + batch.length;
    } catch (e) {
      setTranslateError(e instanceof Error ? e.message : "Couldn't translate.");
    } finally {
      translateRunningRef.current = false;
      setTranslating(false);
    }
  }, [session.id, translationLang]);

  // Continuous translation: initialise the window on enable, then translate new
  // lines (debounced) regardless of which pane is active. Reset when disabled.
  useEffect(() => {
    if (!translationOn) {
      if (translateStart !== null) {
        setTranslateStart(null);
        translateNextRef.current = 0;
        setTranslations({});
      }
      return;
    }
    if (translateStart === null) {
      const s = Math.max(0, segments.length - TRANSLATE_WINDOW);
      translateNextRef.current = s;
      setTranslateStart(s);
      return;
    }
    if (segments.length <= translateNextRef.current) return;
    const t = setTimeout(() => void runTranslation(), TRANSLATE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [segments, translationOn, translateStart, translationLang, runTranslation]);

  // ---- Section nodes -----------------------------------------------------
  const boundaryMap = useMemo(
    () => new Map(recordingBoundaries.map((b) => [b.index, b.kind])),
    [recordingBoundaries],
  );

  // Record-related controls (capture button, device, mic mix-in, language).
  // Rendered pinned at the top of the Transcript tab so they stay reachable
  // even when the transcript fills the height.
  const recordControls = (
    <>
      {recording ? (
        <button
          type="button"
          onClick={() => transcriber.stop()}
          className="inline-flex items-center gap-1.5 rounded bg-red-600 px-2.5 py-1.5 text-sm text-white hover:bg-red-500"
        >
          <Square size={14} /> Stop
        </button>
      ) : starting ? (
        <button
          type="button"
          disabled
          className="inline-flex cursor-wait items-center gap-1.5 rounded bg-amber-500 px-2.5 py-1.5 text-sm font-medium text-black"
        >
          <Loader2 size={14} className="animate-spin" /> Starting…
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
    </>
  );

  const transcriptNode = (
    <div className="flex h-full flex-col">
      {/* Pinned record controls — stay visible as the transcript scrolls. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2">
        {recordControls}
      </div>
      <div ref={transcriptRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
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
          {segments.map((seg, i) => {
            const boundary = boundaryMap.get(i);
            return (
            <div key={seg.id}>
              {boundary === "resume" && (
                <div className="my-3 flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted">
                  <div className="h-px flex-1 bg-border" />
                  Recording resumed
                  <div className="h-px flex-1 bg-border" />
                </div>
              )}
              {boundary === "reconnect" && (
                <div className="my-3 flex items-center gap-2 text-[10px] uppercase tracking-wide text-amber-600 dark:text-amber-500">
                  <div className="h-px flex-1 bg-amber-500/30" />
                  <RefreshCw size={11} aria-hidden="true" />
                  Reconnected
                  <div className="h-px flex-1 bg-amber-500/30" />
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
                  <span className="text-text">
                    <HighlightedText text={seg.text} />
                  </span>
                </div>
              </div>
            </div>
            );
          })}
          {interim && (
            <div className="flex gap-2 text-sm">
              <span className="w-10 shrink-0" />
              <span className="min-w-0 flex-1 italic text-muted">{interim}</span>
            </div>
          )}
        </div>
      )}
      </div>
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
        {/* Proactive help rides along on the analysis pass: shown as cards when
            the assistant judges it can materially help right now. */}
        {suggestions.length > 0 && (
          <div className="mb-3 space-y-2">
            {suggestions.map((s) => (
              <div key={s.id} className="rounded border border-accent/40 bg-accent/5 px-3 py-2">
                <div className="mb-1 flex items-center gap-1.5">
                  <Lightbulb size={12} className="text-accent" />
                  <span className="text-[11px] font-medium uppercase tracking-wide text-muted">
                    Suggestion
                  </span>
                  <span className="ml-auto text-[10px] text-muted">
                    {new Date(s.at).toLocaleTimeString(undefined, {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setSuggestions((prev) => prev.filter((x) => x.id !== s.id))
                    }
                    aria-label="Dismiss"
                    className="text-muted hover:text-red-500"
                  >
                    <X size={12} />
                  </button>
                </div>
                <div className="prose prose-sm dark:prose-invert max-w-none text-[13px]">
                  <Markdown>{s.text}</Markdown>
                </div>
              </div>
            ))}
          </div>
        )}
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
                      <HighlightedText text={it.content} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );

  // A linked Teams meeting + WorkIQ lets the user build the recap from the
  // meeting's own transcript — no local recording needed.
  const canSummarizeFromTranscript =
    (settings?.mcp_enabled?.workiq ?? false) && session.external_meeting != null;

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
      topicIssueNumber={topicIssueNumber}
      canGenerate={segments.length > 0}
      canSummarizeFromTranscript={canSummarizeFromTranscript}
      onSummarizeFromTranscript={() => void generateFromTranscript()}
      transcriptScraping={transcriptScraping}
    />
  );

  const notesNode = (
    <NotesSection
      text={notes}
      setText={setNotes}
      saving={notesSaving}
      saved={notesSaved}
      onUpload={(file) => api.meetings.uploadAttachment(session.id, file)}
      defaultPreview={isEnded}
    />
  );

  const assistantNode = chat ? (
    <LiveChatSection
      chat={chat}
      onChatUpdated={() => {
        void api.chats.get(chat.id)
          .then(setChat)
          .catch(() => {});
      }}
      onArchived={() => setChat(null)}
    />
  ) : null;

  const translationNode = (
    <TranslationSection
      items={translationItems}
      translations={translations}
      startIndex={translateStart ?? Math.max(0, translationItems.length - TRANSLATE_WINDOW)}
      lang={translationLang}
      loading={translating}
      error={translateError}
      onRestart={restartTranslation}
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
  // Optional tabs are gated by the session's enabled features; the assistant
  // also needs its chat to exist. Summary is always available — its actions
  // (generate from the recording vs. from the Teams transcript) light up based
  // on what data is available.
  const tabs: LiveTab[] = [
    { id: "transcript", label: "Transcript" },
    ...(insightsOn
      ? [
          {
            id: "insights",
            label: "Live insights",
            badge: insights.length + suggestions.length || null,
          },
        ]
      : []),
    ...(assistantOn && chat ? [{ id: "assistant", label: "Assistant" }] : []),
    ...(translationOn ? [{ id: "translation", label: "Translation" }] : []),
    ...(notesOn ? [{ id: "notes", label: notes.trim() ? "Notes ●" : "Notes" }] : []),
    { id: "summary", label: hasSummary ? "Summary ●" : "Summary" },
    { id: "context", label: "Context" },
  ];

  function renderSection(id: string): React.ReactNode {
    switch (id) {
      case "transcript":
        return transcriptNode;
      case "insights":
        return insightsNode;
      case "assistant":
        return assistantNode;
      case "translation":
        return translationNode;
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
        <label className="inline-flex items-center gap-1 text-[11px] text-muted">
          Topic
          <TopicPicker
            topics={allTopics}
            value={session.topic_id}
            onChange={(id) => void applyTopic(id)}
          />
        </label>

        <div className="ml-auto flex items-center gap-2">
          <FeaturePicker
            options={FEATURE_OPTIONS}
            value={features}
            onChange={(next) => void applyFeatures(next)}
          />
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
          {onArchived && (
            <button
              type="button"
              onClick={() => void archive()}
              disabled={busy}
              aria-label="Archive session"
              data-tooltip="Archive session"
              className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-text disabled:opacity-60"
            >
              <Archive size={15} />
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
        focus={panelFocus}
      />

      {helpOpen && <LiveAudioHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
