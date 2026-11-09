import { useState } from "react";
import type { SettingsSection } from "./settingsSection";
import { pickSystem, type SystemSettings } from "./systemSettings";

/**
 * System tab: limits and the command runner. The Model tab edits the prompt
 * budgeting part of the same object.
 */
export function useSystemSettings() {
  const [sys, setSys] = useState<SystemSettings | null>(null);
  const [dockerAvailable, setDockerAvailable] = useState(false);
  const section: SettingsSection = {
    hydrate: (s) => {
      setSys(pickSystem(s));
      setDockerAvailable(s.docker_available);
    },
    payload: () => ({ ...(sys ?? {}) }),
    snapshot: sys,
  };
  return { sys, setSys, dockerAvailable, section };
}

export type SystemSettingsState = ReturnType<typeof useSystemSettings>;
