import { useCallback } from "react";
import { usePersistedState } from "./resizeCore";

/**
 * "Show" toggles of the agent workflow timeline. One JSON blob in localStorage
 * so a user's choices stick across agents and app restarts.
 */
export type ShowPrefs = {
  system: boolean;
  assistant: boolean;
  thinking: boolean;
  tool: boolean;
  lifecycle: boolean;
  /** Fold a finished turn's steps behind a one-line summary above its answer. */
  fold: boolean;
};

export const SHOW_PREFS_KEY = "precursor:agent-show-prefs";

// System (the big base prompt) is noise by default.
export const DEFAULT_SHOW_PREFS: ShowPrefs = {
  system: false,
  assistant: true,
  thinking: true,
  tool: true,
  lifecycle: true,
  fold: true,
};

/** Stored prefs over the defaults, or `null` when the blob is corrupt. */
export function parseShowPrefs(raw: string): ShowPrefs | null {
  try {
    return { ...DEFAULT_SHOW_PREFS, ...(JSON.parse(raw) as Partial<ShowPrefs>) };
  } catch {
    return null;
  }
}

const serializeShowPrefs = (prefs: ShowPrefs): string => JSON.stringify(prefs);

/** The persisted prefs and a toggle for one of them. */
export function useShowPrefs(): [ShowPrefs, (key: keyof ShowPrefs) => void] {
  const [prefs, setPrefs] = usePersistedState(
    SHOW_PREFS_KEY,
    DEFAULT_SHOW_PREFS,
    parseShowPrefs,
    serializeShowPrefs,
  );
  const toggle = useCallback(
    (key: keyof ShowPrefs) => setPrefs((p) => ({ ...p, [key]: !p[key] })),
    [setPrefs],
  );
  return [prefs, toggle];
}
