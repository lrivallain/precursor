import type { Collection, CollectionAccent } from "./types";

/**
 * Collection accent palette. Like SECTION_COLORS these are full Tailwind class
 * strings — never build them dynamically — so Tailwind keeps them at build time.
 */
export interface CollectionColor {
  /** Small dot / chip in the switcher and menus. */
  dot: string;
  /** Text accent for the active collection name. */
  text: string;
  /** Tinted chip background + text (settings list, badges). */
  chip: string;
}

export const COLLECTION_ACCENTS: CollectionAccent[] = [
  "sky",
  "emerald",
  "amber",
  "violet",
  "rose",
  "cyan",
  "slate",
];

export const COLLECTION_COLORS: Record<CollectionAccent, CollectionColor> = {
  sky: {
    dot: "bg-sky-500",
    text: "text-sky-600 dark:text-sky-400",
    chip: "bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/30",
  },
  emerald: {
    dot: "bg-emerald-500",
    text: "text-emerald-600 dark:text-emerald-400",
    chip: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30",
  },
  amber: {
    dot: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
    chip: "bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30",
  },
  violet: {
    dot: "bg-violet-500",
    text: "text-violet-600 dark:text-violet-400",
    chip: "bg-violet-500/10 text-violet-700 dark:text-violet-300 border-violet-500/30",
  },
  rose: {
    dot: "bg-rose-500",
    text: "text-rose-600 dark:text-rose-400",
    chip: "bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/30",
  },
  cyan: {
    dot: "bg-cyan-500",
    text: "text-cyan-600 dark:text-cyan-400",
    chip: "bg-cyan-500/10 text-cyan-700 dark:text-cyan-300 border-cyan-500/30",
  },
  slate: {
    dot: "bg-slate-500",
    text: "text-slate-600 dark:text-slate-400",
    chip: "bg-slate-500/10 text-slate-700 dark:text-slate-300 border-slate-500/30",
  },
};

export function collectionColor(accent: string | null | undefined): CollectionColor {
  return COLLECTION_COLORS[(accent as CollectionAccent) ?? "sky"] ?? COLLECTION_COLORS.sky;
}

const STORAGE_KEY = "precursor.collection";

/** Last selected collection id, or null when unset/invalid. */
export function readStoredCollectionId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const id = Number.parseInt(raw, 10);
    return Number.isFinite(id) ? id : null;
  } catch {
    return null;
  }
}

export function writeStoredCollectionId(id: number | null): void {
  try {
    if (id == null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    // Private-mode / disabled storage: selection just won't persist.
  }
}

/**
 * Pick the collection to start in: the stored one when it still exists,
 * otherwise the default, otherwise the first available.
 */
export function pickInitialCollection(
  collections: Collection[],
  storedId: number | null,
): Collection | null {
  if (collections.length === 0) return null;
  const stored = collections.find((c) => c.id === storedId);
  if (stored) return stored;
  return collections.find((c) => c.is_default) ?? collections[0];
}
