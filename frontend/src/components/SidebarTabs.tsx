import type { ComponentType } from "react";

export interface SidebarTab<Id extends string, Group extends string> {
  id: Id;
  label: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  group: Group;
}

export interface SidebarTabsProps<Id extends string, Group extends string> {
  /** Ordered group headings; tabs are bucketed under their `group`. */
  groups: readonly Group[];
  tabs: ReadonlyArray<SidebarTab<Id, Group>>;
  active: Id;
  onSelect: (id: Id) => void;
  className?: string;
}

/**
 * Grouped, icon + label vertical tab navigation with an active accent rail.
 * Extracted from SettingsPanel; reusable for any grouped settings-style nav.
 */
export function SidebarTabs<Id extends string, Group extends string>({
  groups,
  tabs,
  active,
  onSelect,
  className = "w-52 shrink-0 border-r border-border overflow-y-auto py-2",
}: SidebarTabsProps<Id, Group>) {
  return (
    <nav className={className}>
      {groups.map((group) => {
        const inGroup = tabs.filter((t) => t.group === group);
        // Skip a group with nothing in it — a caller may declare a group whose
        // members are contributed dynamically (e.g. plugin settings pages), and
        // a lone heading over empty space reads as something failing to load.
        if (inGroup.length === 0) return null;
        return (
        <div key={group} className="mb-2">
          <div className="px-3 py-1 text-[10px] uppercase tracking-wide text-muted">
            {group}
          </div>
          {inGroup
            .map(({ id, label, icon: Icon }) => {
              const isActive = active === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => onSelect(id)}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left border-l-2 ${
                    isActive
                      ? "border-accent bg-surface text-text"
                      : "border-transparent text-text/80 hover:bg-surface"
                  }`}
                >
                  <Icon size={14} className={isActive ? "text-accent" : "text-muted"} />
                  <span className="truncate">{label}</span>
                </button>
              );
            })}
        </div>
        );
      })}
    </nav>
  );
}
