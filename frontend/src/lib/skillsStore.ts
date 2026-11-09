import { api } from "./api";
import { ListResourceStore, useStoreVersion } from "./externalStore";
import { expandSkillReferences } from "./skillRefs";
import type { Skill } from "./types";

class SkillsStore extends ListResourceStore<Skill> {
  protected readonly label = "skills";

  protected fetchAll(): Promise<Skill[]> {
    return api.skills.list();
  }

  byName(name: string): Skill | undefined {
    return this.items.find((s) => s.name === name);
  }

  /**
   * Substitute `/skill-name` references anywhere in `text` with the matching
   * *active* skill's instructions. Returns `text` unchanged when nothing
   * resolves, so callers can cheaply detect "no expansion happened".
   */
  expandReferences(text: string): string {
    const table = new Map(
      this.items.filter((s) => s.active).map((s) => [s.name, s.instructions]),
    );
    return expandSkillReferences(text, table);
  }
}

export const skillsStore = new SkillsStore();

export function useSkills(): Skill[] {
  useStoreVersion(skillsStore);
  return skillsStore.all();
}
