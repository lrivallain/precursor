import { useEffect } from "react";
import { useSyncExternalStore } from "react";
import { api } from "./api";
import { loadPluginEntries } from "./pluginLoader";
import type { PluginDescriptor } from "./types";

type Listener = () => void;

/**
 * The frontend contributions published by installed, enabled plugins.
 *
 * Kept in a store rather than in `App` state because toggling a plugin has to
 * take effect *everywhere at once* — the sidebar rail, the home launcher, the
 * command palette and the router all derive from this list — and the toggle
 * lives in a settings modal several trees away. Reloading the page would work
 * and is what this replaces; it also closed the modal out from under the user.
 *
 * `refresh()` re-imports any newly available plugin bundle before publishing,
 * so a section is never announced before the code that renders it exists.
 */
class PluginStore {
  private descriptors: PluginDescriptor[] | null = null;
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

  /** `null` until the first fetch resolves. */
  current(): PluginDescriptor[] | null {
    return this.descriptors;
  }

  /** Fetch descriptors and import the bundles they point at. */
  async refresh(): Promise<void> {
    if (this.loading) return this.loading;
    this.loading = (async () => {
      try {
        const list = await api.plugins.list();
        // Import first: loading a plugin's module is what runs its
        // `registerSection`, so publishing before that would briefly announce a
        // section with no implementation behind it.
        await loadPluginEntries(list);
        this.descriptors = list;
      } catch {
        // A plugin surface that fails must never break the core app: fall back
        // to "no plugins" rather than leaving the app stuck loading.
        this.descriptors = this.descriptors ?? [];
      } finally {
        this.loading = null;
        this.notify();
      }
    })();
    return this.loading;
  }
}

export const pluginStore = new PluginStore();

/** Subscribe to the descriptor list, fetching it on first use. */
export function usePluginDescriptors(): PluginDescriptor[] | null {
  useSyncExternalStore(pluginStore.subscribe, pluginStore.getSnapshot, pluginStore.getSnapshot);
  useEffect(() => {
    if (pluginStore.current() === null) void pluginStore.refresh();
  }, []);
  return pluginStore.current();
}
