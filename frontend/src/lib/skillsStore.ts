import { useSyncExternalStore } from "react";
import { api } from "./api";
import type { Skill } from "./types";

type Listener = () => void;

class SkillsStore {
  private skills: Skill[] = [];
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

  all(): Skill[] {
    return this.skills;
  }

  byName(name: string): Skill | undefined {
    return this.skills.find((s) => s.name === name);
  }

  async load(): Promise<void> {
    try {
      this.skills = await api.skills.list();
      this.loaded = true;
      this.notify();
    } catch (err) {
      console.error("Failed to load skills", err);
    }
  }

  async ensureLoaded(): Promise<void> {
    if (!this.loaded) await this.load();
  }
}

export const skillsStore = new SkillsStore();

export function useSkills(): Skill[] {
  useSyncExternalStore(
    skillsStore.subscribe,
    skillsStore.getSnapshot,
    skillsStore.getSnapshot,
  );
  return skillsStore.all();
}
