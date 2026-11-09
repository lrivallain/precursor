import { useSyncExternalStore } from "react";
import { Emitter } from "./externalStore";

/**
 * Expanded-sidebar section navigation style. "rail" shows an always-visible
 * vertical icon rail; "tabs" keeps the horizontal, scrollable switcher.
 */
export type NavStyle = "rail" | "tabs";

const STORAGE_KEY = "precursor:sidebar:navStyle";

function readInitial(): NavStyle {
  if (typeof window === "undefined") return "rail";
  return window.localStorage.getItem(STORAGE_KEY) === "tabs" ? "tabs" : "rail";
}

// Module-level store so every consumer (the sidebar toggle, the home rail)
// reflects the same choice live within a tab — not just after a remount.
let current: NavStyle = readInitial();
const changes = new Emitter();
const { subscribe } = changes;

function getSnapshot(): NavStyle {
  return current;
}

export function setSidebarNavStyle(next: NavStyle): void {
  if (next === current) return;
  current = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore quota / privacy-mode failures */
    }
  }
  changes.emit();
}

/** Read the persisted nav style plus a setter; stays in sync across instances. */
export function useSidebarNavStyle(): [NavStyle, (next: NavStyle) => void] {
  const value = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return [value, setSidebarNavStyle];
}
