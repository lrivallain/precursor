import { useMemo, useState } from "react";
import { Radio } from "lucide-react";
import type { MeetingSession, MeetingSessionCreate, TopicNode } from "../lib/types";
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
