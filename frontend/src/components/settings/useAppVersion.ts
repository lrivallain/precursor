import { useState } from "react";
import { api } from "../../lib/api";
import type { SettingsSection } from "./settingsSection";

/** The running Precursor version, shown in the panel footer. */
export function useAppVersion() {
  const [version, setVersion] = useState<string | null>(null);
  const section: SettingsSection = {
    load: async () => {
      try {
        setVersion((await api.system.getVersion()).version);
      } catch {
        setVersion(null);
      }
    },
  };
  return { version, section };
}
