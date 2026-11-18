import { usePickedSettings } from "./usePickedSettings";
import { changedUpdates, pickUpdates } from "./updateSettings";

/** Updates tab: channel, background checks and their notifications. */
export function useUpdateSettings() {
  return usePickedSettings(pickUpdates, changedUpdates);
}

export type UpdateSettingsState = ReturnType<typeof useUpdateSettings>;
