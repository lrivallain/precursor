import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, SlidersHorizontal } from "lucide-react";

export interface FeatureOption {
  id: string;
  label: string;
  description?: string;
}

/**
 * Multi-select dropdown to enable/disable optional Live features for a session.
 * Mirrors the app's other pickers (TopicPicker) with checkbox rows.
 */
export function FeaturePicker({
  options,
  value,
  onChange,
  disabled,
}: {
  options: FeatureOption[];
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const enabled = new Set(value);
  const count = options.filter((o) => enabled.has(o.id)).length;

  function toggle(id: string): void {
    const next = enabled.has(id) ? value.filter((v) => v !== id) : [...value, id];
    onChange(next);
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded border border-border bg-bg px-2 py-1 text-[12px] disabled:opacity-50"
      >
        <SlidersHorizontal size={12} className="text-muted" />
        <span>Features{count ? ` · ${count}` : ""}</span>
        <ChevronDown size={12} className="text-muted" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-64 rounded-md border border-border bg-surface shadow-lg">
          <ul className="max-h-72 overflow-y-auto p-1 text-[12px]">
            {options.map((o) => {
              const on = enabled.has(o.id);
              return (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => toggle(o.id)}
                    className="flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-bg"
                  >
                    <span
                      className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border ${
                        on ? "border-accent bg-accent text-white" : "border-border"
                      }`}
                    >
                      {on && <Check size={10} />}
                    </span>
                    <span className="min-w-0">
                      <span className={on ? "font-medium" : ""}>{o.label}</span>
                      {o.description && (
                        <span className="block text-[11px] text-muted">{o.description}</span>
                      )}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
