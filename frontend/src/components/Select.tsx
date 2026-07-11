import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface Props {
  value: string;
  onChange: (value: string) => void;
  options: readonly SelectOption[];
  disabled?: boolean;
  ariaLabel?: string;
  /** Shown on the trigger when no option matches the current value. */
  placeholder?: string;
  /** "md" (default) matches form selects; "sm" matches compact inline pickers. */
  size?: "sm" | "md";
  /** Stretch the trigger + popover to the container width (form fields). */
  fullWidth?: boolean;
  /** Force the search box on/off. Defaults to on when there are > 8 options. */
  searchable?: boolean;
  /** Popover horizontal alignment relative to the trigger. */
  align?: "left" | "right";
}

/**
 * App-wide dropdown picker: a styled trigger + popover list, matching the
 * searchable {@link TopicPicker}. Replaces native ``<select>`` everywhere so
 * dropdowns look and behave consistently (theme-aware, checkmarks, optional
 * search) instead of using the OS-rendered control.
 */
export function Select({
  value,
  onChange,
  options,
  disabled,
  ariaLabel,
  placeholder = "Select…",
  size = "md",
  fullWidth,
  searchable,
  align = "left",
}: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value) ?? null;
  const showSearch = searchable ?? options.length > 8;

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  }, [options, query]);

  const sm = size === "sm";
  const triggerClass = sm
    ? "px-2 py-1 text-[11px]"
    : "px-2 py-1.5 text-sm";

  return (
    <div ref={ref} className={`relative ${fullWidth ? "w-full" : "inline-block"}`}>
      <button
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          setQuery("");
          setOpen((v) => !v);
        }}
        className={`flex items-center gap-1 rounded border border-border bg-surface text-text outline-none focus:border-accent disabled:opacity-60 ${triggerClass} ${
          fullWidth ? "w-full" : "max-w-[15rem]"
        }`}
      >
        <span className={`flex-1 truncate text-left ${current ? "" : "text-muted"}`}>
          {current ? current.label : placeholder}
        </span>
        <ChevronDown size={sm ? 11 : 13} className="shrink-0 text-muted" />
      </button>
      {open && (
        <div
          className={`absolute z-30 mt-1 rounded-md border border-border bg-surface shadow-lg ${
            fullWidth ? "w-full" : "min-w-[11rem]"
          } ${align === "right" ? "right-0" : "left-0"}`}
        >
          {showSearch && (
            <div className="border-b border-border p-1.5">
              <input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search…"
                className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
              />
            </div>
          )}
          <ul
            role="listbox"
            className={`max-h-64 overflow-y-auto p-1 ${sm ? "text-[11px]" : "text-sm"}`}
          >
            {filtered.map((o) => (
              <li key={o.value}>
                <button
                  type="button"
                  role="option"
                  aria-selected={o.value === value}
                  disabled={o.disabled}
                  onClick={() => {
                    onChange(o.value);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg disabled:opacity-40 ${
                    o.value === value ? "text-accent" : ""
                  }`}
                >
                  <span className="truncate">{o.label}</span>
                  {o.value === value && <Check size={12} className="shrink-0" />}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="px-2 py-1.5 text-muted">No matches.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
