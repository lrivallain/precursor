import { useState } from "react";
import type { Settings } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";

/** Presets tab: ordered model fallbacks, grouped by category. */
export function usePresetsSettings() {
  const [modelFallbacks, setModelFallbacks] = useState<Settings["model_fallbacks"]>({ categories: [] });
  const section: SettingsSection = {
    hydrate: (s) => setModelFallbacks(s.model_fallbacks ?? { categories: [] }),
    payload: () => ({ model_fallbacks: modelFallbacks }),
    snapshot: modelFallbacks,
  };
  return { modelFallbacks, setModelFallbacks, section };
}
