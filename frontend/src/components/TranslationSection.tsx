import { useEffect, useMemo, useRef } from "react";
import { Check, Languages, Loader2, RefreshCw } from "lucide-react";
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

export interface TranslationItem {
  id: number;
  speaker: string | null;
  colorClass: string;
  time: string;
  text: string;
}

/**
 * Live translation view, laid out like the transcript (colored speaker labels +
 * timestamps). Presentational: the translation loop runs in LiveView so it keeps
 * going even when this tab isn't the active pane. Auto-scrolls to the newest line.
 */
export function TranslationSection({
  items,
  translations,
  startIndex,
  lang,
  loading,
  error,
  onRestart,
}: {
  items: TranslationItem[];
  translations: Record<number, string>;
  startIndex: number;
  lang: string;
  loading: boolean;
  error: string | null;
  onRestart: (lang: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => items.slice(startIndex), [items, startIndex]);
  const translatedCount = shown.filter((it) => translations[it.id]).length;
  const pending = shown.length - translatedCount;

  // Auto-scroll to the bottom as lines/translations arrive (and on mount).
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown.length, translatedCount]);

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
          <Select value={lang} onChange={onRestart} options={LANGS} ariaLabel="Target language" />
          <button
            type="button"
            onClick={() => onRestart(lang)}
            data-tooltip="Re-translate the latest lines"
            aria-label="Re-translate"
            className="rounded border border-border p-1.5 text-muted hover:bg-surface hover:text-accent"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
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
