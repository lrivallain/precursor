import type { ReactNode } from "react";
import { Select } from "../Select";
import type { UpdateSettingsState } from "./useUpdateSettings";

const CHANNELS = [
  { value: "stable", label: "Releases" },
  { value: "nightly", label: "Nightly" },
] as const;

function Toggle({
  checked,
  onChange,
  disabled,
  label,
  children,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
  children: ReactNode;
}) {
  return (
    <label className={`flex items-start gap-2 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-accent"
      />
      <span>
        <span className="block text-sm">{label}</span>
        <span className="block text-[11px] text-muted">{children}</span>
      </span>
    </label>
  );
}

export function UpdatesTab({ s }: { s: UpdateSettingsState }) {
  const { prefs, setPrefs } = s;
  if (!prefs) return null;
  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted">
        Used by the menu-bar app (<code>precursor tray</code>) and{" "}
        <code>precursor service update</code>. Updates are never installed on
        their own: you take them from the menu.
      </p>

      <section className="space-y-1">
        <h3 className="text-sm font-medium mb-2">Update channel</h3>
        <Select
          value={prefs.update_channel}
          onChange={(value) => setPrefs({ ...prefs, update_channel: value as typeof prefs.update_channel })}
          ariaLabel="Update channel"
          fullWidth
          options={CHANNELS}
        />
        <p className="text-[11px] text-muted">
          Releases are the tagged versions. Nightly is rebuilt from every change
          to <code>main</code>: newest features, least tested.
        </p>
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Background checks</h3>
        <Toggle
          label="Check for updates automatically"
          checked={prefs.update_checks_enabled}
          onChange={(next) => setPrefs({ ...prefs, update_checks_enabled: next })}
        >
          Looks for a new build every half hour. When off, use “Check for
          updates” in the menu.
        </Toggle>
        <Toggle
          label="Notify me when an update is available"
          checked={prefs.update_notifications_enabled}
          disabled={!prefs.update_checks_enabled}
          onChange={(next) => setPrefs({ ...prefs, update_notifications_enabled: next })}
        >
          A system notification, so Focus and Do Not Disturb apply and you can
          manage it in your system's notification settings. When off, the menu
          still shows the update.
        </Toggle>
      </section>
    </div>
  );
}
