import { useState } from "react";
import type { Settings, SettingsUpdate } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

/**
 * A tab whose form is a slice picked from the loaded settings and saved as the
 * fields that changed. `afterSave` runs once Save succeeded with what was sent.
 */
export function usePickedSettings<T>(
  pick: (s: Settings) => T,
  changed: (saved: T | null, current: T | null) => SettingsUpdate,
  afterSave?: (sent: SettingsUpdate) => void,
) {
  const [saved, setSaved] = useState<T | null>(null);
  const [prefs, setPrefs] = useState<T | null>(null);
  const adopt = (next: T): T => {
    setSaved(next);
    setPrefs(next);
    return next;
  };
  const section: SettingsSection = {
    hydrate: (s) => void adopt(pick(s)),
    payload: () => changed(saved, prefs),
    snapshot: prefs,
    onSaved: (updated, sent) => {
      afterSave?.(sent);
      return adopt(pick(updated));
    },
  };
  return { prefs, setPrefs, section };
}
