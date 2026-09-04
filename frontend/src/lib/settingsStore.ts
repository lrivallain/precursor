import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Settings } from "./types";

type Listener = () => void;

class SettingsStore {
  private settings: Settings | null = null;
  private loaded = false;
  // Whether the first fetch has *finished*, successfully or not. Distinct from
  // `loaded` (which gates retries): consumers need to tell "not known yet" from
  // "known off", or a boolean flag like `agents_enabled` reads as disabled for
  // the width of the request and the UI flashes a "turn this on" empty state.
  private settled = false;
  private loading: Promise<void> | null = null;
  private version = 0;
  private listeners = new Set<Listener>();

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  getSnapshot = (): number => this.version;

  private notify(): void {
    this.version++;
    for (const l of this.listeners) l();
  }

  current(): Settings | null {
    return this.settings;
  }

  set(settings: Settings | null): void {
    this.settings = settings;
    this.settled = true;
    this.notify();
  }

  /** True once the first fetch has resolved — success or failure. */
  isSettled(): boolean {
    return this.settled;
  }

  async load(): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        this.settings = await api.settings.get();
        this.loaded = true;
      } catch (err) {
        console.warn("Failed to load settings", err);
      } finally {
        // Notified from `finally` so a failed fetch also releases subscribers
        // waiting on `isSettled` instead of pinning them on a spinner.
        this.settled = true;
        this.loading = null;
        this.notify();
      }
    })();
    return this.loading;
  }

  async ensureLoaded(): Promise<void> {
    if (!this.loaded) await this.load();
  }
}

export const settingsStore = new SettingsStore();

export function useSettings(): Settings | null {
  useSyncExternalStore(
    settingsStore.subscribe,
    settingsStore.getSnapshot,
    settingsStore.getSnapshot,
  );
  useEffect(() => {
    void settingsStore.ensureLoaded();
  }, []);
  return settingsStore.current();
}

/**
 * Whether settings have resolved at least once. Pair it with `useSettings`
 * before acting on a feature flag: until this is true a `false` flag only means
 * "not fetched yet", and gating UI on it would advertise the feature as off.
 */
export function useSettingsReady(): boolean {
  useSyncExternalStore(
    settingsStore.subscribe,
    settingsStore.getSnapshot,
    settingsStore.getSnapshot,
  );
  useEffect(() => {
    void settingsStore.ensureLoaded();
  }, []);
  return settingsStore.isSettled();
}
