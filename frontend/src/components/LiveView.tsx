import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleHelp, Mic, Radio, Square, Trash2 } from "lucide-react";
import type { MeetingSegment, MeetingSession, TopicNode } from "../lib/types";
import { api } from "../lib/api";
import { useSettings } from "../lib/settingsStore";
import {
  listAudioInputDevices,
  useConversationTranscriber,
  type AudioInputDevice,
} from "../lib/useConversationTranscriber";
import { useConfirm } from "./ConfirmDialog";
import { LiveAudioHelp } from "./LiveAudioHelp";

// Common BCP-47 tags for the mid-session language switcher.
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

// Stable per-speaker accent, keyed off the diarization label.
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

interface LiveViewProps {
  session: MeetingSession;
  topics: TopicNode[];
  onUpdated: (session: MeetingSession) => void;
  onDeleted: () => void | Promise<void>;
}

/**
 * Live meeting session view: capture controls (device picker + language),
 * the diarized transcript, and session lifecycle actions. Audio is captured in
 * the browser and streamed to Azure; finalized phrases are persisted as they
 * arrive so the transcript survives reloads.
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

  const transcriptRef = useRef<HTMLDivElement>(null);
  const restartRef = useRef(false);

  const isEnded = session.status === "ended";

  const topicTitle = useMemo(() => {
    if (session.topic_id == null) return null;
    return flattenTopics(topics).find((t) => t.id === session.topic_id)?.title ?? null;
  }, [topics, session.topic_id]);

  // Persist each finalized phrase, then reflect the server row locally so the
  // transcript survives reloads. Falls back to a local-only row on write error.
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

  // Load the existing transcript when opening a session (resume/reload).
  useEffect(() => {
    let cancelled = false;
    setSegments([]);
    setInterim("");
    void api
      .listMeetingSegments(session.id)
      .then((rows) => {
        if (!cancelled) setSegments(rows);
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

  // Language changed mid-recording → Azure needs a fresh recognizer, so cycle
  // stop→start once the previous one has torn down.
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

  const recording = transcriber.listening;

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

      <div ref={transcriptRef} className="flex-1 overflow-y-auto px-4 py-4">
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

      {helpOpen && <LiveAudioHelp onClose={() => setHelpOpen(false)} />}
    </div>
  );
}
