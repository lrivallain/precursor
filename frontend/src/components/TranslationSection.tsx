import { useEffect, useRef, useState } from "react";
import { Check, Languages, Loader2, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";

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

const DEBOUNCE_MS = 1500;

/**
 * Live translation: as new transcript lines arrive, the untranslated batch is
 * translated (debounced) and appended into the chosen language. Changing the
 * language re-translates from scratch.
 */
export function TranslationSection({
  sessionId,
  lines,
  defaultLang,
}: {
  sessionId: number;
  /** Formatted transcript lines ("[Speaker] text"), oldest first. */
  lines: string[];
  defaultLang: string;
}) {
  const [lang, setLang] = useState(defaultLang || "en");
  const [out, setOut] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // How many source lines have been translated into the current `out`.
  const translatedRef = useRef(0);
  const langRef = useRef(lang);
  const runningRef = useRef(false);
  const linesRef = useRef(lines);
  linesRef.current = lines;

  // Language change (or a manual reset) restarts the translation from scratch.
  function reset(nextLang: string): void {
    setLang(nextLang);
    langRef.current = nextLang;
    translatedRef.current = 0;
    setOut("");
    setError(null);
  }

  // Live: translate any lines not yet translated, debounced.
  useEffect(() => {
    if (lines.length <= translatedRef.current || runningRef.current) return;
    const t = setTimeout(() => {
      void (async () => {
        if (runningRef.current) return;
        const start = translatedRef.current;
        const end = linesRef.current.length;
        const batch = linesRef.current.slice(start, end).join("\n");
        if (!batch.trim()) return;
        runningRef.current = true;
        setLoading(true);
        setError(null);
        try {
          const res = await api.translateMeeting(sessionId, langRef.current, batch);
          setOut((o) => (o ? o + "\n" : "") + res.text);
          translatedRef.current = end;
        } catch (e) {
          setError(e instanceof Error ? e.message : "Couldn't translate.");
        } finally {
          runningRef.current = false;
          setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [lines, lang, sessionId]);

  const pending = lines.length - translatedRef.current;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-muted">
          <Languages size={12} /> Live translation
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-1 text-[11px] text-muted">
            {loading ? (
              <>
                <Loader2 size={11} className="animate-spin" /> Translating…
              </>
            ) : pending > 0 ? (
              `${pending} pending`
            ) : out ? (
              <>
                <Check size={11} /> Up to date
              </>
            ) : null}
          </span>
          <Select value={lang} onChange={reset} options={LANGS} ariaLabel="Target language" />
          <button
            type="button"
            onClick={() => reset(lang)}
            data-tooltip="Re-translate from scratch"
            aria-label="Re-translate"
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-accent"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {error && <p className="mb-2 text-[12px] text-red-500">{error}</p>}
        {out ? (
          <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-text">{out}</div>
        ) : loading ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={14} className="animate-spin" /> Translating the transcript…
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <Languages size={18} className="mb-2 opacity-70" aria-hidden="true" />
            <p className="max-w-sm">
              The transcript is translated into your language live as the meeting goes. Pick a
              target language above.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
