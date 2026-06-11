export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "precursor:theme";

function systemPrefersDark(): boolean {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  const dark = theme === "dark" || (theme === "system" && systemPrefersDark());
  root.classList.toggle("dark", dark);
}

export function applyInitialTheme(): void {
  const stored = (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
  applyTheme(stored);
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

export function getStoredTheme(): Theme {
  return (localStorage.getItem(STORAGE_KEY) as Theme | null) ?? "system";
}
