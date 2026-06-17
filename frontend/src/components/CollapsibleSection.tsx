import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

/**
 * Collapsible section header used across the sidebar (Topics, Chats, …).
 * A single shared construction so every list folds its sections identically.
 */
export function SectionHeader({
  icon,
  label,
  collapsed,
  onToggle,
}: {
  icon: ReactNode;
  label: string;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="group w-full flex items-center gap-1.5 px-2 py-1 text-[11px] uppercase tracking-wide text-muted hover:text-text"
    >
      <span className="text-muted group-hover:text-text">
        {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
      </span>
      {icon}
      <span>{label}</span>
    </button>
  );
}

/**
 * Tracks which named sections are collapsed and persists the set under
 * `storageKey` so the choice survives reloads. Generic over the section name
 * so it can back any list (topics, chats, …) independently.
 */
export function useCollapsedSections(storageKey: string): {
  collapsed: Set<string>;
  toggle: (key: string) => void;
} {
  const [collapsed, setCollapsed] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return new Set();
      const keys = JSON.parse(raw) as unknown;
      if (!Array.isArray(keys)) return new Set();
      return new Set(keys.filter((k): k is string => typeof k === "string"));
    } catch {
      return new Set();
    }
  });

  const toggle = useCallback(
    (key: string) => {
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(key)) next.delete(key);
        else next.add(key);
        if (typeof window !== "undefined") {
          window.localStorage.setItem(storageKey, JSON.stringify([...next]));
        }
        return next;
      });
    },
    [storageKey],
  );

  return { collapsed, toggle };
}
