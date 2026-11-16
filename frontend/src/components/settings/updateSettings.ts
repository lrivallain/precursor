import type { Settings, SettingsUpdate } from "../../lib/types";

/** The Updates tab's fields: what the menu-bar app checks for, and how loudly. */
export interface UpdateSettings {
  update_channel: Settings["update_channel"];
  update_checks_enabled: boolean;
  update_notifications_enabled: boolean;
}

export function pickUpdates(s: Settings): UpdateSettings {
  return {
    update_channel: s.update_channel,
    update_checks_enabled: s.update_checks_enabled,
    update_notifications_enabled: s.update_notifications_enabled,
  };
}

/**
 * Only the fields the user changed. The channel shown before any choice is the
 * one the running build follows; sending it back unchanged would pin it, and a
 * later nightly install would keep tracking releases.
 */
export function changedUpdates(saved: UpdateSettings | null, current: UpdateSettings | null): SettingsUpdate {
  if (!saved || !current) return {};
  const changed: SettingsUpdate = {};
  if (current.update_channel !== saved.update_channel) changed.update_channel = current.update_channel;
  if (current.update_checks_enabled !== saved.update_checks_enabled) {
    changed.update_checks_enabled = current.update_checks_enabled;
  }
  if (current.update_notifications_enabled !== saved.update_notifications_enabled) {
    changed.update_notifications_enabled = current.update_notifications_enabled;
  }
  return changed;
}
