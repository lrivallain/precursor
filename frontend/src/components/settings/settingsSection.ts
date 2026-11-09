import type { Settings, SettingsUpdate } from "../../lib/types";

/**
 * One tab's contribution to the settings form. Every tab hook returns one, and
 * `useSettingsForm` combines them: the panel mounts only the active tab, but a
 * single Save sends the pending edits of all of them.
 */
export interface SettingsSection {
  /** Seed the tab's form state from the settings loaded when the panel opens. */
  hydrate?: (s: Settings) => void;
  /** Slower loads (catalogues, status probes) run in order after hydration. */
  load?: (s: Settings) => Promise<void>;
  /** The fields this tab sends on Save. */
  payload?: () => SettingsUpdate;
  /** What the dirty check compares; any JSON-serialisable value. */
  snapshot?: unknown;
  /**
   * Called once Save succeeded with the saved settings and what was sent.
   * Returns the tab's snapshot as it stands after the save (secret inputs are
   * cleared, for instance), or `undefined` when it is unchanged.
   */
  onSaved?: (updated: Settings, sent: SettingsUpdate) => unknown;
}

/** Each tab's snapshot once Save succeeded (see `SettingsSection.onSaved`). */
export function savedSnapshots(
  sections: readonly SettingsSection[],
  updated: Settings,
  sent: SettingsUpdate,
): unknown[] {
  return sections.map((section) => {
    const after = section.onSaved?.(updated, sent);
    return after === undefined ? section.snapshot : after;
  });
}

/** Merge the tabs' payloads; `api_keys` from several tabs are combined. */
export function mergeSectionPayloads(sections: readonly SettingsSection[]): SettingsUpdate {
  const merged: SettingsUpdate = {};
  const apiKeys: Record<string, string> = {};
  for (const section of sections) {
    const { api_keys, ...rest } = section.payload?.() ?? {};
    Object.assign(merged, rest);
    Object.assign(apiKeys, api_keys);
  }
  if (Object.keys(apiKeys).length > 0) merged.api_keys = apiKeys;
  return merged;
}

/** The form's state as one string, for the unsaved-changes check. */
export function formSnapshot(snapshots: readonly unknown[]): string {
  return JSON.stringify(snapshots);
}
