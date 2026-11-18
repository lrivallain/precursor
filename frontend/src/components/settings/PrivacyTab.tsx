import { useTelemetryStatus } from "../../lib/telemetry";
import { SettingToggle } from "./SettingToggle";
import { withHeartbeat } from "./telemetrySettings";
import type { PrivacySettingsState } from "./usePrivacySettings";

export function PrivacyTab({ s }: { s: PrivacySettingsState }) {
  const { prefs, setPrefs } = s;
  const status = useTelemetryStatus();
  if (!prefs) return null;
  const unavailable = status && !status.available ? status.unavailable_reason : null;
  return (
    <div className="space-y-6">
      <p className="text-[11px] text-muted">
        Anonymous usage data tells the maintainers how many installs there are, which versions
        they run and which features are used. Nothing is sent unless you turn it on, and never
        your conversations, files, paths, names, accounts or tokens.
      </p>
      {unavailable && (
        <p className="rounded border border-border px-3 py-2 text-[11px] text-muted">
          Nothing is sent from this install: {unavailable}
        </p>
      )}

      <section className="space-y-3">
        <h3 className="text-sm font-medium">Anonymous usage data</h3>
        <SettingToggle
          label="Send a daily heartbeat"
          checked={prefs.telemetry_enabled}
          onChange={(next) => setPrefs(withHeartbeat(prefs, next))}
        >
          Once a day: the version, update channel, install type, OS, Python version, which
          features are on, and usage counts in rough buckets (for example “10-49”). A random id,
          reset when you turn this off, lets installs be counted once.
        </SettingToggle>
        <SettingToggle
          label="Send error reports"
          checked={prefs.telemetry_errors_enabled}
          disabled={!prefs.telemetry_enabled}
          onChange={(next) => setPrefs({ ...prefs, telemetry_errors_enabled: next })}
        >
          When Precursor logs an error: the exception type and where it happened in Precursor's
          own code (file, function, line). Never the error message, which can hold your data.
          At most one report per distinct error a day.
        </SettingToggle>
      </section>

      {status && (
        <section className="space-y-1">
          <h3 className="text-sm font-medium">What the heartbeat looks like</h3>
          <p className="text-[11px] text-muted">The exact body sent today, with a placeholder id.</p>
          <pre className="max-h-80 overflow-auto rounded bg-black/5 p-2 font-mono text-[11px] dark:bg-white/5">
            {status.preview}
          </pre>
        </section>
      )}
    </div>
  );
}
