import type { SidebarMode } from "../components/Sidebar";

/**
 * Per-section color scheme, shared across the app so a section reads the same
 * everywhere (home cards, sidebar tabs, …). Values are full Tailwind class
 * strings — never build them dynamically — so Tailwind keeps them at build time.
 */
export interface SectionColor {
  /** Icon badge: background tint + icon text color. */
  icon: string;
  /** Home card border + tint when its start surface is open. */
  activeCard: string;
  /** Home card hover accent (border + tint). */
  hoverCard: string;
  /** Filled primary action button (home "New …"). */
  primaryBtn: string;
  /** Accent text for arrows / hover chrome. */
  accentText: string;
  /** Active tab/rail button: background tint + text color. */
  activeTab: string;
}

export const SECTION_COLORS: Record<SidebarMode, SectionColor> = {
  topics: {
    icon: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
    activeCard: "border-sky-500/60 bg-sky-500/10",
    hoverCard: "hover:border-sky-500/50 hover:bg-sky-500/5",
    primaryBtn:
      "bg-sky-500/15 text-sky-700 hover:bg-sky-500/25 dark:text-sky-300 border border-sky-500/30",
    accentText: "text-sky-600 dark:text-sky-400",
    activeTab: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  },
  chats: {
    icon: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    activeCard: "border-emerald-500/60 bg-emerald-500/10",
    hoverCard: "hover:border-emerald-500/50 hover:bg-emerald-500/5",
    primaryBtn:
      "bg-emerald-500/15 text-emerald-700 hover:bg-emerald-500/25 dark:text-emerald-300 border border-emerald-500/30",
    accentText: "text-emerald-600 dark:text-emerald-400",
    activeTab: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  },
  live: {
    icon: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    activeCard: "border-rose-500/60 bg-rose-500/10",
    hoverCard: "hover:border-rose-500/50 hover:bg-rose-500/5",
    primaryBtn:
      "bg-rose-500/15 text-rose-700 hover:bg-rose-500/25 dark:text-rose-300 border border-rose-500/30",
    accentText: "text-rose-600 dark:text-rose-400",
    activeTab: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  },
  agents: {
    icon: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    activeCard: "border-violet-500/60 bg-violet-500/10",
    hoverCard: "hover:border-violet-500/50 hover:bg-violet-500/5",
    primaryBtn:
      "bg-violet-500/15 text-violet-700 hover:bg-violet-500/25 dark:text-violet-300 border border-violet-500/30",
    accentText: "text-violet-600 dark:text-violet-400",
    activeTab: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  },
  workspaces: {
    icon: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    activeCard: "border-amber-500/60 bg-amber-500/10",
    hoverCard: "hover:border-amber-500/50 hover:bg-amber-500/5",
    primaryBtn:
      "bg-amber-500/15 text-amber-700 hover:bg-amber-500/25 dark:text-amber-300 border border-amber-500/30",
    accentText: "text-amber-600 dark:text-amber-400",
    activeTab: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  },
  kanban: {
    icon: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
    activeCard: "border-cyan-500/60 bg-cyan-500/10",
    hoverCard: "hover:border-cyan-500/50 hover:bg-cyan-500/5",
    primaryBtn:
      "bg-cyan-500/15 text-cyan-700 hover:bg-cyan-500/25 dark:text-cyan-300 border border-cyan-500/30",
    accentText: "text-cyan-600 dark:text-cyan-400",
    activeTab: "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
  },
};
