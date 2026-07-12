import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Languages, Loader2, RefreshCw } from "lucide-react";
import { api } from "../lib/api";
import { Select } from "./Select";

// Target languages for live translation of the transcript.
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

const DEBOUNCE_MS = 1200;
// When translation is (re)started mid-meeting, only the last few lines are
// translated first — the backlog is skipped so the user gets instant, relevant
// output rather than waiting for the whole conversation.
const INITIAL_WINDOW = 6;

export interface TranslationItem {
  id: number;
  speaker: string | null;
  colorClass: string;
  time: string;
  text: string;
}

/**
 * Live translation, laid out like the transcript: one line per segment with the
 * (colored) speaker label and time, translated text streamed in as the meeting
 * progresses. Only the newest lines are translated on (re)start.
 */
export function TranslationSection({
  sessionId,
  items,
  defaultLang,
}: {
  sessionId: number;
  items: TranslationItem[];
  defaultLang: string;
}) {
  const [lang, setLang] = useState(defaultLang || "en");
  const [translations, setTranslations] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const langRef = useRef(lang);
  const runningRef = useRef(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  // Index of the first segment we translate — fixed when the tab (re)starts, so
  // the earlier backlog is left untranslated.
  const startRef = useRef<number>(Math.max(0, items.length - INITIAL_WINDOW));
  // Next segment index to translate.
  const nextRef = useRef<number>(startRef.current);

  function restart(nextLang: string): void {
    setLang(nextLang);
    langRef.current = nextLang;
    startRef.current = Math.max(0, itemsRef.current.length - INITIAL_WINDOW);
    nextRef.current = startRef.current;
    setTranslations({});
    setError(null);
  }

  // Translate any not-yet-translated lines (debounced), oldest-first.
  useEffect(() => {
    if (items.length <= nextRef.current || runningRef.current) return;
    const t = setTimeout(() => {
      void (async () => {
        if (runningRef.current || itemsRef.current.length <= nextRef.current) return;
        const start = nextRef.current;
        const batch = itemsRef.current.slice(start);
        runningRef.current = true;
        setLoading(true);
        setError(null);
        try {
          const res = await api.translateMeeting(
            sessionId,
            langRef.current,
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
          nextRef.current = start + batch.length;
        } catch (e) {
          setError(e instanceof Error ? e.message : "Couldn't translate.");
        } finally {
          runningRef.current = false;
          setLoading(false);
        }
      })();
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [items, lang, sessionId]);

  const shown = useMemo(() => items.slice(startRef.current), [items]);
  const pending = items.length - nextRef.current;

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
            ) : shown.length > 0 ? (
              <>
                <Check size={11} /> Up to date
              </>
            ) : null}
          </span>
          <Select value={lang} onChange={restart} options={LANGS} ariaLabel="Target language" />
          <button
            type="button"
            onClick={() => restart(lang)}
            data-tooltip="Re-translate the latest lines"
            aria-label="Re-translate"
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-accent"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {error && <p className="mb-2 text-[12px] text-red-500">{error}</p>}
        {shown.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted">
            <Languages size={18} className="mb-2 opacity-70" aria-hidden="true" />
            <p className="max-w-sm">
              The transcript is translated into your language live as the meeting goes. Pick a
              target language above.
            </p>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-2">
            {shown.map((it) => {
              const translated = translations[it.id];
              return (
                <div key={it.id} className="flex gap-2 text-sm">
                  <span className="w-10 shrink-0 pt-0.5 text-right text-[11px] tabular-nums text-muted">
                    {it.time}
                  </span>
                  <div className="min-w-0 flex-1">
                    {it.speaker && (
                      <span className={`mr-1.5 text-[12px] font-medium ${it.colorClass}`}>
                        {it.speaker}
                      </span>
                    )}
                    <span className={translated ? "text-text" : "italic text-muted"}>
                      {translated ?? it.text}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
