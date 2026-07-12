import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Mic } from "lucide-react";
import type { AudioInputDevice } from "../lib/useConversationTranscriber";

/**
 * A searchable audio-input picker (combobox), styled to match {@link TopicPicker}
 * so the Live capture controls feel consistent. Value is the deviceId; an empty
 * string means the browser's default input.
 */
export function DevicePicker({
  devices,
  value,
  onChange,
  disabled,
}: {
  devices: AudioInputDevice[];
  value: string;
  onChange: (deviceId: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const current = devices.find((d) => d.deviceId === value) ?? null;

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
    return q ? devices.filter((d) => d.label.toLowerCase().includes(q)) : devices;
  }, [devices, query]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setQuery("");
          setOpen((v) => !v);
        }}
        className="flex max-w-[15rem] items-center gap-1 rounded border border-border bg-bg px-2 py-1 text-[11px] disabled:opacity-50"
      >
        <Mic size={11} className="shrink-0 text-muted" />
        <span className={`truncate ${current ? "" : "text-muted"}`}>
          {current ? current.label : "Default input"}
        </span>
        <ChevronDown size={11} className="shrink-0 text-muted" />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-64 rounded-md border border-border bg-surface shadow-lg">
          <div className="border-b border-border p-1.5">
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search inputs…"
              className="w-full rounded border border-border bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
          </div>
          <ul className="max-h-56 overflow-y-auto p-1 text-[11px]">
            <li>
              <button
                type="button"
                onClick={() => {
                  onChange("");
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left hover:bg-bg ${
                  value === "" ? "text-accent" : "text-muted"
                }`}
              >
                Default input {value === "" && <Check size={12} />}
              </button>
            </li>
            {filtered.map((d) => (
              <li key={d.deviceId}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(d.deviceId);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-bg ${
                    value === d.deviceId ? "text-accent" : ""
                  }`}
                >
                  <span className="truncate">{d.label}</span>
                  {value === d.deviceId && <Check size={12} className="shrink-0" />}
                </button>
              </li>
            ))}
            {filtered.length === 0 && (
              <li className="px-2 py-1.5 text-muted">No matching inputs.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
