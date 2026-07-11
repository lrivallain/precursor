import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleHelp, Mic, Radio, RefreshCw, Send, Square, Trash2 } from "lucide-react";
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
import {
  listAudioInputDevices,
  useConversationTranscriber,
  type AudioInputDevice,
} from "../lib/useConversationTranscriber";
import { useConfirm } from "./ConfirmDialog";
import { LiveAudioHelp } from "./LiveAudioHelp";

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

function formatOffset(ms: number | null): string {
  if (ms == null) return "";
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function flattenTopics(tree: TopicNode[]): { id: number; title: string }[] {
  const out: { id: number; title: string }[] = [];
  const walk = (nodes: TopicNode[]): void => {
    for (const n of nodes) {
      out.push({ id: n.id, title: n.title });
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
}

/**
 * Live meeting session view: capture, the diarized transcript, rolling live
 * insights, and a direct Q&A box. Audio is captured in the browser and streamed
 * to Azure; finalized phrases persist as they arrive. Insights are re-derived
 * from the rolling window on a silence/interval cadence while recording.
 */
export function LiveView({ session, topics, onUpdated, onDeleted }: LiveViewProps) {
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

  const [insights, setInsights] = useState<MeetingInsight[]>([]);
  const [analyzing, setAnalyzing] = useState(false);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);

  const transcriptRef = useRef<HTMLDivElement>(null);
  const restartRef = useRef(false);
  const analyzingRef = useRef(false);
  const lastAnalyzedRef = useRef(0);
  const segCountRef = useRef(0);
  segCountRef.current = segments.length;

  const isEnded = session.status === "ended";

  const topicTitle = useMemo(() => {
    if (session.topic_id == null) return null;
    return flattenTopics(topics).find((t) => t.id === session.topic_id)?.title ?? null;
  }, [topics, session.topic_id]);

  const handleFinalSegment = useCallback(
    (seg: { text: string; speakerLabel: string | null; offsetMs: number }) => {
      void (async () => {
        try {
          const saved = await api.appendMeetingSegment(session.id, {
            text: seg.text,
            speaker_label: seg.speakerLabel,
            offset_ms: seg.offsetMs,
          });
          setSegments((prev) => [...prev, saved]);
        } catch {
          setSegments((prev) => [
            ...prev,
            {
              id: -Date.now(),
              session_id: session.id,
              speaker_label: seg.speakerLabel,
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
    lastAnalyzedRef.current = 0;
    void api
      .listMeetingSegments(session.id)
      .then((rows) => {
        if (!cancelled) {
          setSegments(rows);
          lastAnalyzedRef.current = rows.length;
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

  async function setStatus(next: "active" | "ended"): Promise<void> {
    if (busy) return;
    if (next === "ended" && transcriber.listening) transcriber.stop();
    setBusy(true);
    try {
      const updated = await api.updateMeetingSession(session.id, { status: next });
      onUpdated(updated);
    } finally {
      setBusy(false);
    }
  }

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
      setQuestion("");
    } catch {
      setAnswer("Sorry — the assistant couldn't answer that.");
    } finally {
      setAsking(false);
    }
  }

  const groupedInsights = useMemo(() => {
    return KIND_META.map((meta) => ({
      ...meta,
      items: insights.filter((i) => i.kind === meta.kind),
    })).filter((g) => g.items.length > 0);
  }, [insights]);

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
          <select
            value={deviceId}
            onChange={(e) => setDeviceId(e.target.value)}
            disabled={recording || !sttReady}
            aria-label="Input device"
            className="max-w-[14rem] rounded border border-border bg-surface px-2 py-1 text-sm text-text outline-none focus:border-accent disabled:opacity-60"
          >
            <option value="">Default input</option>
            {devices.map((d) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label}
              </option>
            ))}
          </select>
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

        <select
          value={session.language ?? ""}
          onChange={(e) => void applyLanguage(e.target.value)}
          aria-label="Meeting language"
          className="rounded border border-border bg-surface px-2 py-1 text-sm text-text outline-none focus:border-accent"
        >
          {LANGUAGES.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[11px] text-muted">Topic: {topicTitle ?? "none"}</span>
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

      <div className="flex min-h-0 flex-1">
        {/* Transcript */}
        <div ref={transcriptRef} className="flex-1 overflow-y-auto px-4 py-4">
          {segments.length === 0 && !interim ? (
            <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
              <Radio size={20} className="mb-2 opacity-70" aria-hidden="true" />
              <p className="mb-1 font-medium text-text">No transcript yet</p>
              <p className="max-w-sm">
                Pick the input carrying your meeting audio, then press Record.
                Each phrase is transcribed with a speaker label and saved as you
                go.
              </p>
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-2">
              {segments.map((seg) => (
                <div key={seg.id} className="flex gap-2 text-sm">
                  <span className="w-10 shrink-0 pt-0.5 text-right text-[11px] tabular-nums text-muted">
                    {formatOffset(seg.offset_ms)}
                  </span>
                  <div className="min-w-0 flex-1">
                    {seg.speaker_label && (
                      <span
                        className={`mr-1.5 text-[12px] font-medium ${speakerColor(
                          seg.speaker_label,
                        )}`}
                      >
                        {seg.speaker_label}
                      </span>
                    )}
                    <span className="text-text">{seg.text}</span>
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

        {/* Insights + Q&A */}
        <aside className="flex w-80 shrink-0 flex-col border-l border-border">
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

          {/* Q&A */}
          <div className="border-t border-border p-3">
            {answer && (
              <div className="mb-2 max-h-40 overflow-y-auto rounded bg-surface px-2 py-1.5 text-[13px] text-text">
                {answer}
              </div>
            )}
            <div className="flex items-end gap-1.5">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void ask();
                  }
                }}
                rows={2}
                placeholder="Ask the assistant…"
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
        </aside>
      </div>

      {helpOpen && <LiveAudioHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
