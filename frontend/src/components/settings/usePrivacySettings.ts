import { notifyTelemetryChanged } from "../../lib/telemetry";
import { usePickedSettings } from "./usePickedSettings";
import { changedTelemetry, pickTelemetry } from "./telemetrySettings";

/** Privacy tab: whether this install sends anonymous usage data and error reports. */
export function usePrivacySettings() {
  return usePickedSettings(pickTelemetry, changedTelemetry, (sent) => {
    if ("telemetry_enabled" in sent || "telemetry_errors_enabled" in sent) notifyTelemetryChanged();
  });
}

export type PrivacySettingsState = ReturnType<typeof usePrivacySettings>;
