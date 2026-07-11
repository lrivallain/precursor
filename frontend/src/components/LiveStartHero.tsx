import { useMemo, useState } from "react";
import { Radio } from "lucide-react";
import type { MeetingSession, MeetingSessionCreate, TopicNode } from "../lib/types";
import { useSettings } from "../lib/settingsStore";
import { api } from "../lib/api";

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

interface TopicOption {
  id: number;
  label: string;
}

// Flatten the topic tree into indented options for the attach-topic picker.
function flattenTopics(tree: TopicNode[]): TopicOption[] {
  const out: TopicOption[] = [];
  const walk = (nodes: TopicNode[], depth: number): void => {
    for (const n of nodes) {
      out.push({ id: n.id, label: `${"\u00a0\u00a0".repeat(depth)}${n.title}` });
      if (n.children.length) walk(n.children, depth + 1);
    }
  };
  walk(tree, 0);
  return out;
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

  const topicOptions = useMemo(() => flattenTopics(topics), [topics]);

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

  return (
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col justify-center gap-4 p-8">
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

        <label className="flex flex-col gap-1 text-[12px] text-muted">
          Attach a topic for context <span className="opacity-70">(optional)</span>
          <select
            value={topicId ?? ""}
            onChange={(e) => setTopicId(e.target.value ? Number(e.target.value) : null)}
            className="rounded border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none focus:border-accent"
          >
            <option value="">No topic</option>
            {topicOptions.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-[12px] text-muted">
          Meeting language
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="rounded border border-border bg-surface px-2 py-1.5 text-sm text-text outline-none focus:border-accent"
          >
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.value === "" && defaultLang
                  ? `Use configured default (${defaultLang})`
                  : l.label}
              </option>
            ))}
          </select>
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
