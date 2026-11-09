import { useSyncExternalStore } from "react";

/**
 * Building blocks for the app's module-level stores read through
 * `useSyncExternalStore`. Reuse these instead of hand-rolling a listener set,
 * a version counter and `subscribe` / `getSnapshot` in every store.
 *
 * - `Emitter`: for stores written as plain module functions.
 * - `VersionedStore`: base class for class-based stores whose snapshot is a
 *   change counter (read the data through the store's own getters).
 * - `ListResourceStore`: a `VersionedStore` holding a list fetched from the API.
 */

export type Listener = () => void;

/** A listener set plus a change counter, shaped for `useSyncExternalStore`. */
export class Emitter {
  private readonly listeners = new Set<Listener>();
  private version = 0;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  /** Bumped on every `emit`, so it works as a snapshot for version-based stores. */
  getVersion = (): number => this.version;

  emit = (): void => {
    this.version++;
    for (const listener of this.listeners) listener();
  };
}

/** Base for class-based stores: `subscribe`, `getSnapshot` and `notify()`. */
export abstract class VersionedStore {
  private readonly changes = new Emitter();

  subscribe = this.changes.subscribe;

  getSnapshot = this.changes.getVersion;

  protected notify(): void {
    this.changes.emit();
  }
}

/** Re-render the calling component whenever `store` notifies. */
export function useStoreVersion(store: VersionedStore): number {
  return useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
}

/**
 * A list fetched once from the API and shared app-wide (roles, skills...).
 * `load()` refetches; `ensureLoaded()` fetches only the first time.
 */
export abstract class ListResourceStore<T> extends VersionedStore {
  protected items: T[] = [];
  private loaded = false;

  protected abstract fetchAll(): Promise<T[]>;

  /** Used in the console message when a fetch fails (e.g. `"roles"`). */
  protected abstract readonly label: string;

  all(): T[] {
    return this.items;
  }

  async load(): Promise<void> {
    try {
      this.items = await this.fetchAll();
      this.loaded = true;
      this.notify();
    } catch (err) {
      console.error(`Failed to load ${this.label}`, err);
    }
  }

  async ensureLoaded(): Promise<void> {
    if (!this.loaded) await this.load();
  }
}
