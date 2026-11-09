import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { modelsStore } from "../../lib/modelsStore";
import { settingsStore } from "../../lib/settingsStore";
import type { Settings } from "../../lib/types";
import { useConfirm } from "../ConfirmDialog";
import { formSnapshot, mergeSectionPayloads, savedSnapshots, type SettingsSection } from "./settingsSection";

/**
 * The settings as last loaded or saved, shared by every tab. `adopt` is for a
 * tab that saves on its own (backup, IQ, the OpenAI proxy): it updates the
 * snapshot and the app-wide store without touching the other tabs' edits.
 */
export function useLoadedSettings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const adopt = useCallback((next: Settings): void => {
    setSettings(next);
    settingsStore.set(next);
  }, []);
  return { settings, setSettings, adopt };
}

export type LoadedSettings = ReturnType<typeof useLoadedSettings>;

/**
 * Load, Save, the unsaved-changes check and closing for the settings panel.
 * The tabs own their fields and describe them as `SettingsSection`s.
 */
export function useSettingsForm({
  loaded,
  sections,
  onClose: closeNow,
}: {
  loaded: LoadedSettings;
  sections: readonly SettingsSection[];
  onClose: () => void;
}) {
  const { settings, setSettings } = loaded;
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  // The load runs once; it reads the sections through a ref so it sees their
  // setters without re-running when they re-render.
  const sectionsRef = useRef(sections);
  sectionsRef.current = sections;

  useEffect(() => {
    void (async () => {
      const s = await api.settings.get();
      setSettings(s);
      for (const section of sectionsRef.current) section.hydrate?.(s);
      settingsStore.set(s);
      // Each load reports its own errors, so one slow or failing source (an
      // MCP server probe, say) never holds back the others.
      await Promise.allSettled(sectionsRef.current.map((section) => section.load?.(s)));
    })();
  }, [setSettings]);

  const snapshot = formSnapshot(sections.map((section) => section.snapshot));
  // Captured on the render right after the initial load populates the form.
  const baseline = useRef<string | null>(null);
  useEffect(() => {
    if (settings && baseline.current === null) baseline.current = snapshot;
  }, [settings, snapshot]);

  async function save(): Promise<void> {
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const payload = mergeSectionPayloads(sections);
      const updated = await api.settings.update(payload);
      setSettings(updated);
      modelsStore.applySettings(updated);
      settingsStore.set(updated);
      baseline.current = formSnapshot(savedSnapshots(sections, updated, payload));
      setSaved(true);
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  const onClose = useCloseGuard(baseline, snapshot, closeNow);

  return {
    saving,
    saveError,
    /** True while the form still matches what was just saved. */
    savedNotice: saved && baseline.current === snapshot,
    save,
    onClose,
  };
}

// Close (button, backdrop or Esc), asking first when there are unsaved edits.
function useCloseGuard(
  baseline: { current: string | null },
  snapshot: string,
  closeNow: () => void,
): () => Promise<void> {
  const confirmAction = useConfirm();
  const confirming = useRef(false);
  const onClose = useCallback(async (): Promise<void> => {
    if (confirming.current) return;
    const dirty = baseline.current !== null && baseline.current !== snapshot;
    if (dirty) confirming.current = true;
    const keep = dirty && !(await confirmAction({
      title: "Discard unsaved changes?",
      message: "You have unsaved settings. Closing now will discard them.",
      confirmLabel: "Discard changes",
      cancelLabel: "Keep editing",
      variant: "warning",
    }));
    confirming.current = false;
    if (keep) return;
    closeNow();
  }, [baseline, snapshot, confirmAction, closeNow]);

  // Esc closes the modal — convention shared with most settings dialogs
  // (VS Code, Linear, GitHub).
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") void onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return onClose;
}
