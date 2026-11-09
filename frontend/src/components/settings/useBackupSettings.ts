import { useState } from "react";
import { api } from "../../lib/api";
import type { BackupRunResult } from "../../lib/types";
import type { SettingsSection } from "./settingsSection";
import type { LoadedSettings } from "./useSettingsForm";

/** Backup tab: folder backup preferences and a manual "run now". */
export function useBackupSettings({ adopt }: Pick<LoadedSettings, "adopt">) {
  const [enabled, setEnabled] = useState(false);
  const [dir, setDir] = useState("");
  const [retention, setRetention] = useState(7);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<BackupRunResult | null>(null);

  // Persist the current backup prefs, then run one immediately so the button
  // acts on exactly what the user sees (not a stale saved value). Refreshes the
  // settings snapshot afterwards so the last-run status updates in place.
  async function runNow(): Promise<void> {
    setRunning(true);
    setRunResult(null);
    try {
      adopt(await api.settings.update({ backup_enabled: enabled, backup_dir: dir, backup_retention: retention }));
      setRunResult(await api.settings.runBackupNow());
      adopt(await api.settings.get());
    } catch (e) {
      setRunResult({
        ok: false,
        status: "error",
        detail: e instanceof Error ? e.message : String(e),
        db_snapshot: null,
        blobs_copied: 0,
        blobs_total: 0,
        definitions_archive: null,
      });
    } finally {
      setRunning(false);
    }
  }

  const section: SettingsSection = {
    hydrate: (s) => {
      setEnabled(s.backup_enabled);
      setDir(s.backup_dir);
      setRetention(s.backup_retention);
    },
    payload: () => ({ backup_enabled: enabled, backup_dir: dir, backup_retention: retention }),
    snapshot: [enabled, dir, retention],
  };
  return { enabled, setEnabled, dir, setDir, retention, setRetention, running, runResult, runNow, section };
}

export type BackupSettings = ReturnType<typeof useBackupSettings>;
