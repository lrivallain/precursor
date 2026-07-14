import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Settings } from "./types";

type Listener = () => void;

class SettingsStore {
  private settings: Settings | null = null;
  private loaded = false;
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
    this.notify();
  }

  async load(): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        this.settings = await api.settings.get();
        this.loaded = true;
        this.notify();
      } catch (err) {
        console.warn("Failed to load settings", err);
      } finally {
        this.loading = null;
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
