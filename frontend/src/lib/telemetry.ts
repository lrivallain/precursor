import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { call } from "./apiClient";
import { Emitter } from "./externalStore";
import type * as Api from "./generated/apiTypes";

export type TelemetryStatus = Api.TelemetryStatus;

// Fired when the choice changes, so the banner and the settings tab agree.
const changes = new Emitter();

export function notifyTelemetryChanged(): void {
  changes.emit();
}

export function fetchTelemetryStatus(): Promise<TelemetryStatus> {
  return call("GET /api/telemetry");
}

/** Records the user's answer; turning the heartbeat off turns error reports off too. */
export async function saveTelemetryChoice(enabled: boolean): Promise<void> {
  await api.settings.update(enabled ? { telemetry_enabled: true } : { telemetry_enabled: false, telemetry_errors_enabled: false });
  notifyTelemetryChanged();
}

/** The status, reloaded whenever the choice changes. Null while loading or on error. */
export function useTelemetryStatus(): TelemetryStatus | null {
  const [status, setStatus] = useState<TelemetryStatus | null>(null);
  const load = useCallback(() => {
    fetchTelemetryStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);
  useEffect(() => {
    load();
    return changes.subscribe(load);
  }, [load]);
  return status;
}
