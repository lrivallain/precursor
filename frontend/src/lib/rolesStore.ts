import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Role } from "./types";

type Listener = () => void;

class RolesStore {
  private roles: Role[] = [];
  private version = 0;
  private listeners = new Set<Listener>();
  private loaded = false;

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

  all(): Role[] {
    return this.roles;
  }

  byId(id: number | null | undefined): Role | undefined {
    if (id == null) return undefined;
    return this.roles.find((r) => r.id === id);
  }

  /** Case-insensitive name lookup, used by `/role <name>`. */
  byName(name: string): Role | undefined {
    const q = name.trim().toLowerCase();
    return this.roles.find((r) => r.name.toLowerCase() === q);
  }

  defaultRole(): Role | undefined {
    return this.roles.find((r) => r.is_default);
  }

  async load(): Promise<void> {
    try {
      this.roles = await api.roles.list();
      this.loaded = true;
      this.notify();
    } catch (err) {
      console.error("Failed to load roles", err);
    }
  }

  async ensureLoaded(): Promise<void> {
    if (!this.loaded) await this.load();
  }
}

export const rolesStore = new RolesStore();

export function useRoles(): Role[] {
  useSyncExternalStore(
    rolesStore.subscribe,
    rolesStore.getSnapshot,
    rolesStore.getSnapshot,
  );
  return rolesStore.all();
}
