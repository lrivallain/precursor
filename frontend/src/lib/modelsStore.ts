import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { api } from "./api";
import { resolveDefaultModel } from "./conversationModel";
import type { LLMModel, Settings } from "./types";

type Listener = () => void;

// Providers add and retire models upstream, and the desktop webview never
// reloads — a fetch-once catalog would stay frozen for the lifetime of the
// app, hiding new models and offering retired ones.
const STALE_AFTER_MS = 5 * 60 * 1000;

class ModelsStore {
  private catalogs = new Map<string, { models: LLMModel[]; fetchedAt: number }>();
  private errors = new Map<string, string>();
  private provider = "github_copilot";
  private currentId: string | null = null;
  // The default model's provider: a default category's preset can sit on
  // another provider than `llm_provider`.
  private currentProvider: string | null = null;
  private loading = new Map<string, Promise<void>>();
  private generations = new Map<string, number>();
  private requests = new Map<string, symbol>();
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

  all(provider = this.provider): LLMModel[] {
    return this.catalogs.get(provider)?.models ?? [];
  }

  hasLoaded(provider: string): boolean {
    return this.catalogs.has(provider);
  }

  byIdOrNull(id: string | null | undefined, provider = this.provider): LLMModel | null {
    if (!id) return null;
    return this.all(provider).find((model) => model.id === id) ?? null;
  }

  error(provider: string): string | null {
    return this.errors.get(provider) ?? null;
  }

  current(): LLMModel | null {
    return this.byIdOrNull(this.currentId, this.currentProviderId());
  }

  /** Provider whose catalog lists the current model. */
  currentProviderId(): string {
    return this.currentProvider ?? this.provider;
  }

  setCurrent(id: string | null, provider: string | null = null): void {
    if (this.currentId === id && this.currentProvider === provider) return;
    this.currentId = id;
    this.currentProvider = provider;
    this.notify();
  }

  applySettings(settings: Settings | null | undefined): void {
    if (settings && this.provider !== settings.llm_provider) {
      this.provider = settings.llm_provider;
      this.notify();
    }
    const resolved = resolveDefaultModel(settings);
    this.setCurrent(resolved.id, resolved.provider);
  }

  /** Seed the catalog from a list a caller already fetched. */
  adopt(list: LLMModel[], provider = this.provider): void {
    this.generations.set(provider, (this.generations.get(provider) ?? 0) + 1);
    this.catalogs.set(provider, { models: list, fetchedAt: Date.now() });
    this.errors.delete(provider);
    this.notify();
  }

  async load(provider = this.provider): Promise<void> {
    const pending = this.loading.get(provider);
    if (pending) return pending;
    const generation = this.generations.get(provider) ?? 0;
    const token = Symbol();
    this.requests.set(provider, token);
    const request = (async () => {
      try {
        const list = await api.llm.listModels(provider);
        if ((this.generations.get(provider) ?? 0) === generation) this.adopt(list, provider);
      } catch (err) {
        if ((this.generations.get(provider) ?? 0) === generation) {
          this.catalogs.delete(provider);
          this.errors.set(provider, err instanceof Error ? err.message : String(err));
          this.notify();
        }
        console.warn("Failed to load model catalog", err);
      } finally {
        if (this.requests.get(provider) === token) {
          this.loading.delete(provider);
          this.requests.delete(provider);
        }
      }
    })();
    this.loading.set(provider, request);
    return request;
  }

  /** Force a catalog refetch — use after the provider or its token changes. */
  async refresh(provider = this.provider): Promise<void> {
    this.generations.set(provider, (this.generations.get(provider) ?? 0) + 1);
    this.loading.delete(provider);
    return this.load(provider);
  }

  async ensureLoaded(provider = this.provider): Promise<void> {
    const catalog = this.catalogs.get(provider);
    if (!catalog || Date.now() - catalog.fetchedAt > STALE_AFTER_MS) await this.load(provider);
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
  const provider = modelsStore.currentProviderId();
  useEffect(() => {
    void modelsStore.ensureLoaded(provider);
  }, [provider]);
  return modelsStore.current();
}
