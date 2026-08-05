import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { LLMModel, Settings } from "./types";

type Listener = () => void;

// Providers add and retire models upstream, and the desktop webview never
// reloads — a fetch-once catalog would stay frozen for the lifetime of the
// app, hiding new models and offering retired ones.
const STALE_AFTER_MS = 5 * 60 * 1000;

class ModelsStore {
  private models: LLMModel[] = [];
  private byId = new Map<string, LLMModel>();
  private currentId: string | null = null;
  private loaded = false;
  private fetchedAt = 0;
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

  all(): LLMModel[] {
    return this.models;
  }

  byIdOrNull(id: string | null | undefined): LLMModel | null {
    if (!id) return null;
    return this.byId.get(id) ?? null;
  }

  current(): LLMModel | null {
    return this.byIdOrNull(this.currentId);
  }

  setCurrent(id: string | null): void {
    if (this.currentId === id) return;
    this.currentId = id;
    this.notify();
  }

  applySettings(settings: Settings | null | undefined): void {
    this.setCurrent(settings?.llm_model ?? null);
  }

  /** Seed the catalog from a list a caller already fetched. */
  adopt(list: LLMModel[]): void {
    this.models = list;
    this.byId = new Map(list.map((m) => [m.id, m]));
    this.loaded = true;
    this.fetchedAt = Date.now();
    this.notify();
  }

  async load(): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        const list = await api.llm.listModels();
        this.adopt(list);
      } catch (err) {
        // Provider may not be configured; that's OK, just leave list empty.
        console.warn("Failed to load model catalog", err);
      } finally {
        this.loading = null;
      }
    })();
    return this.loading;
  }

  /** Force a catalog refetch — use after the provider or its token changes. */
  async refresh(): Promise<void> {
    return this.load();
  }

  async ensureLoaded(): Promise<void> {
    if (!this.loaded || Date.now() - this.fetchedAt > STALE_AFTER_MS) await this.load();
  }
}

export const modelsStore = new ModelsStore();

export function useModelsVersion(): number {
  return useSyncExternalStore(
    modelsStore.subscribe,
    modelsStore.getSnapshot,
    modelsStore.getSnapshot,
  );
}

export function useCurrentModel(): LLMModel | null {
  useModelsVersion();
  useEffect(() => {
    void modelsStore.ensureLoaded();
  }, []);
  return modelsStore.current();
}
