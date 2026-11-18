import type { Settings, SettingsUpdate } from "../../lib/types";

/** The Privacy tab's fields: the daily heartbeat and, on top of it, error reports. */
export interface TelemetrySettings {
  telemetry_enabled: boolean;
  telemetry_errors_enabled: boolean;
}

export function pickTelemetry(s: Settings): TelemetrySettings {
  return {
    telemetry_enabled: s.telemetry_enabled,
    telemetry_errors_enabled: s.telemetry_errors_enabled,
  };
}

/** Error reports ride on the heartbeat: turning it off turns them off too. */
export function withHeartbeat(current: TelemetrySettings, enabled: boolean): TelemetrySettings {
  return {
    telemetry_enabled: enabled,
    telemetry_errors_enabled: enabled && current.telemetry_errors_enabled,
  };
}

/** Only the fields the user changed: saving either one records an answer. */
export function changedTelemetry(
  saved: TelemetrySettings | null,
  current: TelemetrySettings | null,
): SettingsUpdate {
  if (!saved || !current) return {};
  const changed: SettingsUpdate = {};
  if (current.telemetry_enabled !== saved.telemetry_enabled) {
    changed.telemetry_enabled = current.telemetry_enabled;
  }
  if (current.telemetry_errors_enabled !== saved.telemetry_errors_enabled) {
    changed.telemetry_errors_enabled = current.telemetry_errors_enabled;
  }
  return changed;
}
