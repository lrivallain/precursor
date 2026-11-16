import { useState } from "react";
import type { SettingsSection } from "./settingsSection";
import { changedUpdates, pickUpdates, type UpdateSettings } from "./updateSettings";

/** Updates tab: channel, background checks and their notifications. */
export function useUpdateSettings() {
  const [saved, setSaved] = useState<UpdateSettings | null>(null);
  const [prefs, setPrefs] = useState<UpdateSettings | null>(null);
  const adopt = (next: UpdateSettings): UpdateSettings => {
    setSaved(next);
    setPrefs(next);
    return next;
  };
  const section: SettingsSection = {
    hydrate: (s) => void adopt(pickUpdates(s)),
    payload: () => changedUpdates(saved, prefs),
    snapshot: prefs,
    onSaved: (updated) => adopt(pickUpdates(updated)),
  };
  return { prefs, setPrefs, section };
}

export type UpdateSettingsState = ReturnType<typeof useUpdateSettings>;
