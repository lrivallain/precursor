import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import type { Topic } from "../lib/types";

/**
 * A searchable topic lookup (combobox). Originally built to associate an agent
 * with a topic; shared so the Live meeting session picker matches it exactly.
 */
export function TopicPicker({
  topics,
  value,
  onChange,
  disabled,
}: {
  topics: Topic[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = topics.find((t) => t.id === value) ?? null;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q ? topics.filter((t) => t.title.toLowerCase().includes(q)) : topics;
    return list.slice(0, 50);
  }, [topics, query]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setQuery("");
          setOpen((v) => !v);
        }}
        className="flex items-center gap-1 rounded border border-border bg-bg px-2 py-1 text-[11px] disabled:opacity-50"
      >
        <Search size={11} className="text-muted" />
        <span className={current ? "" : "text-muted"}>{current ? current.title : "None"}</span>
        <ChevronDown size={11} className="text-muted" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-60 rounded-md border border-border bg-surface shadow-lg">
          <div className="border-b border-border p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search topics…"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto p-1 text-[11px]">
            <li>
              <button
                type="button"
                onClick={() => {
                  onChange(null);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left hover:bg-bg ${
                  value === null ? "text-accent" : "text-muted"
                }`}
              >
                None {value === null && <Check size={12} />}
              </button>
            </li>
            {filtered.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(t.id);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg ${
                    value === t.id ? "text-accent" : ""
                  }`}
                >
                  <span className="truncate">{t.title}</span>
                  {value === t.id && <Check size={12} className="shrink-0" />}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="px-2 py-1.5 text-muted">No matching topics.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
