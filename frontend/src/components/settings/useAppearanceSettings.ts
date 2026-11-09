import { useState } from "react";
import { setFont, getStoredFont, type FontChoice } from "../../lib/font";
import { setTheme, getStoredTheme, type Theme } from "../../lib/theme";
import type { SettingsSection } from "./settingsSection";

/** Appearance tab: theme and font (applied on Save) and the quick-actions button. */
export function useAppearanceSettings() {
  // Theme and font start from what this browser shows, not the server copy.
  const [theme, setThemeState] = useState<Theme>(getStoredTheme());
  const [font, setFontState] = useState<FontChoice>(getStoredFont());
  const [showQuickActions, setShowQuickActions] = useState(true);
  const section: SettingsSection = {
    hydrate: (s) => setShowQuickActions(s.show_quick_actions ?? true),
    payload: () => ({ theme, font_family: font, show_quick_actions: showQuickActions }),
    snapshot: [theme, font, showQuickActions],
    onSaved: () => {
      setTheme(theme);
      setFont(font);
      return undefined;
    },
  };
  return { theme, setThemeState, font, setFontState, showQuickActions, setShowQuickActions, section };
}

export type AppearanceSettings = ReturnType<typeof useAppearanceSettings>;
