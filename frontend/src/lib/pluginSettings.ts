/**
 * Read/write a plugin's own settings blob.
 *
 * Core stores one opaque JSON object per plugin and never looks inside, so a
 * plugin's settings panel and its backend agree on the shape without core
 * having to know it. `usePluginSettings` is the hook a panel wants: it loads
 * once, tracks a draft, and saves the whole document.
 */

import { useCallback, useEffect, useState } from "react";
import { api, apiErrorMessage } from "./api";

export interface PluginSettingsState<T> {
  /** `null` until the first load resolves. */
  value: T | null;
  setValue: (next: T) => void;
  save: () => Promise<void>;
  saving: boolean;
  error: string | null;
  /** Whether the draft differs from what was last loaded or saved. */
  dirty: boolean;
}

export function usePluginSettings<T extends Record<string, unknown>>(
  pluginId: string,
  defaults: T,
): PluginSettingsState<T> {
  const [value, setValue] = useState<T | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void api.plugins.settings
      .get(pluginId)
      .then((stored) => {
        if (cancelled) return;
        // Merge over defaults so a plugin can add a key without migrating
        // everyone's stored blob.
        const merged = { ...defaults, ...(stored as Partial<T>) } as T;
        setValue(merged);
        setSaved(JSON.stringify(merged));
      })
      .catch((e) => {
        if (cancelled) return;
        setValue({ ...defaults });
        setError(apiErrorMessage(e, "Failed to load settings"));
      });
    return () => {
      cancelled = true;
    };
    // `defaults` is a literal at the call site; re-running on its identity would
    // loop forever.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pluginId]);

  const save = useCallback(async () => {
    if (value === null) return;
    setSaving(true);
    setError(null);
    try {
      await api.plugins.settings.put(pluginId, value);
      setSaved(JSON.stringify(value));
    } catch (e) {
      setError(apiErrorMessage(e, "Failed to save settings"));
    } finally {
      setSaving(false);
    }
  }, [pluginId, value]);

  return {
    value,
    setValue,
    save,
    saving,
    error,
    dirty: value !== null && JSON.stringify(value) !== saved,
  };
}
