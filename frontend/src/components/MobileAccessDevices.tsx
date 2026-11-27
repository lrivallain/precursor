import { Pencil } from "lucide-react";
import { useState } from "react";
import type { RemoteSession } from "../lib/remoteAccessApi";

interface Props {
  devices: RemoteSession[];
  busy: boolean;
  onRevoke: (id: string) => void;
  onRename: (id: string, label: string) => void;
}

/** The paired mobile devices: rename (pencil, or double-click the name), or sign out. */
export function MobileAccessDevices({ devices, busy, onRevoke, onRename }: Props) {
  if (devices.length === 0) return null;
  return (
    <div className="mb-3">
      <h3 className="text-xs uppercase tracking-wide text-muted mb-1">Paired devices</h3>
      <ul className="text-sm divide-y divide-border">
        {devices.map((d) => (
          <li key={d.id} className="flex items-center gap-2 py-1">
            <DeviceName device={d} onRename={(label) => onRename(d.id, label)} />
            <span className="text-xs text-muted">{new Date(d.last_used_at).toLocaleDateString()}</span>
            <button type="button" className="text-xs underline" disabled={busy} onClick={() => onRevoke(d.id)}>
              Sign out
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DeviceName({ device, onRename }: { device: RemoteSession; onRename: (label: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <>
        <span
          className="flex-1 min-w-0 truncate cursor-text"
          data-tooltip={`${device.label}\nDouble-click to rename`}
          onDoubleClick={() => setDraft(device.label)}
        >
          {device.label}
        </span>
        {/* The keyboard and screen-reader way in (double-click is a shortcut). */}
        <button
          type="button"
          className="shrink-0 rounded p-1 text-muted hover:bg-surface hover:text-text"
          aria-label={`Rename ${device.label}`}
          data-tooltip="Rename"
          onClick={() => setDraft(device.label)}
        >
          <Pencil size={13} />
        </button>
      </>
    );
  }
  const save = () => {
    const label = draft.trim();
    if (label && label !== device.label) onRename(label);
    setDraft(null);
  };
  return (
    <input
      // Focus the field the user just double-clicked into.
      // oxlint-disable-next-line jsx-a11y/no-autofocus
      autoFocus
      aria-label="Device name"
      maxLength={120}
      className="flex-1 min-w-0 rounded border border-accent bg-bg px-1 text-sm"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onFocus={(e) => e.currentTarget.select()}
      onBlur={save}
      onKeyDown={(e) => {
        // Enter also confirms an IME candidate (Chinese, Japanese, Korean...).
        if (e.key === "Enter" && !e.nativeEvent.isComposing) save();
        if (e.key === "Escape") {
          e.stopPropagation();
          setDraft(null);
        }
      }}
    />
  );
}
