import { api } from "./api";
import { ListResourceStore, useStoreVersion } from "./externalStore";
import type { Role } from "./types";

class RolesStore extends ListResourceStore<Role> {
  protected readonly label = "roles";

  protected fetchAll(): Promise<Role[]> {
    return api.roles.list();
  }

  /** Case-insensitive name lookup: a role is referenced by its name. */
  byName(name: string | null | undefined): Role | undefined {
    if (!name) return undefined;
    const q = name.trim().toLowerCase();
    return this.items.find((r) => r.name.toLowerCase() === q);
  }

  defaultRole(): Role | undefined {
    return this.items.find((r) => r.is_default);
  }
}

export const rolesStore = new RolesStore();

export function useRoles(): Role[] {
  useStoreVersion(rolesStore);
  return rolesStore.all();
}
