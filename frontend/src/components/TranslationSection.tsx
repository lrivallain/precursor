import { useState } from "react";
import { Languages, Loader2 } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";
import { Markdown } from "./Markdown";

// Target languages for on-demand translation of the transcript.
const LANGS: { value: string; label: string }[] = [
  { value: "en", label: "English" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "es", label: "Spanish" },
  { value: "it", label: "Italian" },
  { value: "pt", label: "Portuguese" },
  { value: "nl", label: "Dutch" },
  { value: "ja", label: "Japanese" },
  { value: "zh", label: "Chinese" },
  { value: "ar", label: "Arabic" },
];

/**
 * Live translation (on demand): translate the current transcript into a chosen
 * language. Re-run to refresh as the meeting progresses.
 */
export function TranslationSection({
  sessionId,
  canRun,
  defaultLang,
}: {
  sessionId: number;
  canRun: boolean;
  defaultLang: string;
}) {
  const [lang, setLang] = useState(defaultLang || "en");
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.translateMeeting(sessionId, lang);
      setText(res.text);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't translate.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">
          Translation
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Select value={lang} onChange={setLang} options={LANGS} ariaLabel="Target language" />
          <button
            type="button"
            onClick={() => void run()}
            disabled={loading || !canRun}
            data-tooltip={canRun ? undefined : "Record some of the meeting first"}
            className="inline-flex items-center gap-1.5 rounded bg-accent px-2.5 py-1.5 text-[12px] text-white disabled:opacity-50"
          >
            {loading ? <Loader2 size={13} className="animate-spin" /> : <Languages size={13} />}
            {text ? "Retranslate" : "Translate"}
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-[12px] text-red-500">{error}</p>}
        {loading && !text ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Translating the transcript…
          </div>
        ) : text ? (
          <div className="prose prose-sm dark:prose-invert max-w-none whitespace-pre-wrap text-[13px]">
            <Markdown>{text}</Markdown>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <Languages size={18} className="mb-2 opacity-70" aria-hidden="true" />
            <p className="max-w-sm">
              On demand, translate the running transcript into your language. Re-run to refresh
              as the meeting continues.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
