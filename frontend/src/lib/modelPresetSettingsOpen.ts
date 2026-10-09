const MODEL_PRESET_SETTINGS_EVENT = "precursor:model-preset-settings-open";

export function openModelPresetSettings(): void {
  window.dispatchEvent(new Event(MODEL_PRESET_SETTINGS_EVENT));
}

export function subscribeModelPresetSettingsOpen(handler: () => void): () => void {
  window.addEventListener(MODEL_PRESET_SETTINGS_EVENT, handler);
  return () => window.removeEventListener(MODEL_PRESET_SETTINGS_EVENT, handler);
}
