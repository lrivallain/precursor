// Reading-font selection. Mirrors lib/theme.ts: applied instantly via a class
// on <html> and persisted to localStorage for the next page load, while the
// choice is also mirrored into Settings (font_family) so it round-trips with
// the rest of the user's preferences.
export type FontChoice = "system" | "opendyslexic" | "atkinson-hyperlegible" | "lexend";

export interface FontOption {
  id: FontChoice;
  label: string;
  description: string;
}

// Order drives the Settings panel's picker. "system" first (the default),
// then fonts chosen for readability/dyslexia support — see
// public/fonts/LICENSE.md for the bundled faces' attribution.
export const FONT_OPTIONS: FontOption[] = [
  {
    id: "system",
    label: "System default",
    description: "Inter, falling back to the OS UI font.",
  },
  {
    id: "opendyslexic",
    label: "OpenDyslexic",
    description: "Weighted lettering designed to reduce letter confusion for dyslexic readers.",
  },
  {
    id: "atkinson-hyperlegible",
    label: "Atkinson Hyperlegible",
    description: "Braille Institute typeface tuned for maximum character legibility.",
  },
  {
    id: "lexend",
    label: "Lexend",
    description:
      "Reading-fluency typeface: studies found it measurably speeds up reading for low-proficiency and dyslexic readers.",
  },
];

const STORAGE_KEY = "precursor:font";

function className(font: FontChoice): string | null {
  return font === "system" ? null : `font-${font}`;
}

export function applyFont(font: FontChoice): void {
  const root = document.documentElement;
  for (const option of FONT_OPTIONS) {
    const cls = className(option.id);
    if (cls) root.classList.remove(cls);
  }
  const cls = className(font);
  if (cls) root.classList.add(cls);
}

export function applyInitialFont(): void {
  applyFont(getStoredFont());
}

export function setFont(font: FontChoice): void {
  localStorage.setItem(STORAGE_KEY, font);
  applyFont(font);
}

export function getStoredFont(): FontChoice {
  const stored = localStorage.getItem(STORAGE_KEY) as FontChoice | null;
  return stored && FONT_OPTIONS.some((o) => o.id === stored) ? stored : "system";
}
