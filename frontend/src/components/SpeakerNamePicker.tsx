import { useEffect, useMemo, useRef, useState } from "react";
import { Check } from "lucide-react";

/**
 * Inline speaker-name picker for the transcript. Opens a small searchable
 * popover (matching TopicPicker) anchored on the speaker label: the text field
 * doubles as free-text entry and the filter for the candidate list (invitees,
 * prior attendees, names used earlier). The list is never pre-filtered by the
 * current name, so a mislabelled speaker can be re-picked even when already set.
 */
export function SpeakerNamePicker({
  value,
  options,
  color,
  onCommit,
  onCancel,
}: {
  value: string;
  options: string[];
  color?: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Guard so a click-away (blur) that commits doesn't also fire cancel twice.
  const doneRef = useRef(false);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        if (!doneRef.current) {
          doneRef.current = true;
          onCancel();
        }
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [onCancel]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? options.filter((o) => o.toLowerCase().includes(q)) : options;
    return list.slice(0, 50);
  }, [options, query]);

  function commit(name: string): void {
    if (doneRef.current) return;
    doneRef.current = true;
    onCommit(name.trim());
  }

  return (
    <div ref={ref} className="relative mr-1.5 inline-block align-baseline">
      <input
        ref={inputRef}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter") {
            e.preventDefault();
            commit(query || value);
          } else if (e.key === "Escape") {
            e.preventDefault();
            if (!doneRef.current) {
              doneRef.current = true;
              onCancel();
            }
          }
        }}
        placeholder={value}
        aria-label="Speaker name"
        className={`w-32 rounded border border-accent/60 bg-bg px-1 py-0 text-[12px] font-medium outline-none ${color ?? ""}`}
      />
      <div className="absolute z-30 mt-1 w-48 rounded-md border border-border bg-surface shadow-lg">
        <ul className="max-h-56 overflow-y-auto p-1 text-[12px]">
          {filtered.map((o) => (
            <li key={o}>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  commit(o);
                }}
                className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg ${
                  o === value ? "text-accent" : ""
                }`}
              >
                <span className="truncate">{o}</span>
                {o === value && <Check size={12} className="shrink-0" />}
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="px-2 py-1.5 text-muted">
              {query.trim() ? `Use “${query.trim()}”` : "Type a name…"}
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
