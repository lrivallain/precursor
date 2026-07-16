import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Bot,
  FolderGit2,
  MessageSquarePlus,
  MessagesSquare,
  Plus,
  Radio,
  Sparkles,
  SquareKanban,
  X,
} from "lucide-react";
import { api } from "../lib/api";
import type { SidebarMode } from "./Sidebar";
import type { Me } from "../lib/types";
import { PersonaMenu } from "./PersonaMenu";

type HomeKind = "topics" | "chats" | "live" | "agents";

/** Per-section color scheme. Full class strings so Tailwind keeps them. */
interface Palette {
  /** Icon badge background + text. */
  icon: string;
  /** Card border + tint when its start surface is open. */
  active: string;
  /** Card hover accent (border + tint). */
  hover: string;
  /** Filled primary action button. */
  primary: string;
  /** Accent text (arrows, hover chrome). */
  accent: string;
}

interface Section {
  /** Sidebar mode to jump to when visiting the section. */
  mode: SidebarMode;
  /** Present when the section supports starting a new item inline. */
  createKind?: HomeKind;
  title: string;
  description: string;
  /** Label for the "start new" action (create sections only). */
  newLabel?: string;
  /** Label for the "visit section" action. */
  openLabel: string;
  icon: ReactNode;
  color: Palette;
}

interface Props {
  /** Inline start surfaces, shown on the same page under the cards. */
  topicSurface: ReactNode;
  chatSurface: ReactNode;
  liveSurface: ReactNode;
  agentSurface: ReactNode;
  liveEnabled?: boolean;
  /** Whether the Kanban section is available (adds its card). */
  kanbanEnabled?: boolean;
  /** Jump straight into a section's list/surface (leaves the home launcher). */
  onNavigate?: (mode: SidebarMode) => void;
  /** Open the global settings panel (from the persona menu). */
  onOpenSettings?: () => void;
  /** Open the archives panel (from the persona menu). */
  onOpenArchive?: () => void;
}

/**
 * Landing surface shown at `/`. A greeting and a grid of section cards, each of
 * which can both start a new item (when suitable) and jump into the related
 * section — no separate nav row. Every section carries its own color scheme to
 * make the grid easy to scan. Starting a new item reveals that section's start
 * surface right below on the same page; picking it again hides the surface.
 */
export function HomePage({
  topicSurface,
  chatSurface,
  liveSurface,
  agentSurface,
  liveEnabled = true,
  kanbanEnabled = false,
  onNavigate,
  onOpenSettings,
  onOpenArchive,
}: Props) {
  const [me, setMe] = useState<Me | null>(null);
  const [selected, setSelected] = useState<HomeKind | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.me
      .get()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch(() => {
        /* greeting falls back to a generic label */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const firstName = (me?.github?.name || me?.github?.login || "").split(" ")[0];

  const sections: Section[] = [
    {
      mode: "topics",
      createKind: "topics",
      title: "Topics",
      description:
        "Long-lived threads that keep their own history, context, and optional linked issue.",
      newLabel: "New topic",
      openLabel: "Browse topics",
      icon: <MessagesSquare size={20} />,
      color: {
        icon: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
        active: "border-sky-500/60 bg-sky-500/10",
        hover: "hover:border-sky-500/50 hover:bg-sky-500/5",
        primary:
          "bg-sky-500/15 text-sky-700 hover:bg-sky-500/25 dark:text-sky-300 border border-sky-500/30",
        accent: "text-sky-600 dark:text-sky-400",
      },
    },
    {
      mode: "chats",
      createKind: "chats",
      title: "Chats",
      description:
        "Quick, throwaway conversations. Type a prompt and get going in seconds.",
      newLabel: "New chat",
      openLabel: "Browse chats",
      icon: <MessageSquarePlus size={20} />,
      color: {
        icon: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        active: "border-emerald-500/60 bg-emerald-500/10",
        hover: "hover:border-emerald-500/50 hover:bg-emerald-500/5",
        primary:
          "bg-emerald-500/15 text-emerald-700 hover:bg-emerald-500/25 dark:text-emerald-300 border border-emerald-500/30",
        accent: "text-emerald-600 dark:text-emerald-400",
      },
    },
    ...(liveEnabled
      ? [
          {
            mode: "live",
            createKind: "live",
            title: "Live sessions",
            description:
              "Capture a meeting live with transcription, notes, and summaries as it happens.",
            newLabel: "New live session",
            openLabel: "Browse sessions",
            icon: <Radio size={20} />,
            color: {
              icon: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
              active: "border-rose-500/60 bg-rose-500/10",
              hover: "hover:border-rose-500/50 hover:bg-rose-500/5",
              primary:
                "bg-rose-500/15 text-rose-700 hover:bg-rose-500/25 dark:text-rose-300 border border-rose-500/30",
              accent: "text-rose-600 dark:text-rose-400",
            },
          } satisfies Section,
        ]
      : []),
    {
      mode: "agents",
      createKind: "agents",
      title: "Agents",
      description:
        "Hand a task to an autonomous coding agent and follow its progress.",
      newLabel: "New agent",
      openLabel: "Browse agents",
      icon: <Bot size={20} />,
      color: {
        icon: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
        active: "border-violet-500/60 bg-violet-500/10",
        hover: "hover:border-violet-500/50 hover:bg-violet-500/5",
        primary:
          "bg-violet-500/15 text-violet-700 hover:bg-violet-500/25 dark:text-violet-300 border border-violet-500/30",
        accent: "text-violet-600 dark:text-violet-400",
      },
    },
    {
      mode: "workspaces",
      title: "Files",
      description:
        "Browse the workspaces and files backing your sessions.",
      openLabel: "Browse files",
      icon: <FolderGit2 size={20} />,
      color: {
        icon: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
        active: "border-amber-500/60 bg-amber-500/10",
        hover: "hover:border-amber-500/50 hover:bg-amber-500/5",
        primary:
          "bg-amber-500/15 text-amber-700 hover:bg-amber-500/25 dark:text-amber-300 border border-amber-500/30",
        accent: "text-amber-600 dark:text-amber-400",
      },
    },
    ...(kanbanEnabled
      ? [
          {
            mode: "kanban",
            title: "Kanban",
            description:
              "Track linked issues on a board across your projects.",
            openLabel: "Open board",
            icon: <SquareKanban size={20} />,
            color: {
              icon: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
              active: "border-cyan-500/60 bg-cyan-500/10",
              hover: "hover:border-cyan-500/50 hover:bg-cyan-500/5",
              primary:
                "bg-cyan-500/15 text-cyan-700 hover:bg-cyan-500/25 dark:text-cyan-300 border border-cyan-500/30",
              accent: "text-cyan-600 dark:text-cyan-400",
            },
          } satisfies Section,
        ]
      : []),
  ];

  const surfaces: Record<HomeKind, ReactNode> = {
    topics: topicSurface,
    chats: chatSurface,
    live: liveSurface,
    agents: agentSurface,
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex w-full shrink-0 flex-col">
        <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-accent">
              <Sparkles size={18} />
              <span className="text-xs font-medium uppercase tracking-wide">
                Precursor
              </span>
            </div>
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              {firstName ? `Hey ${firstName}!` : "Hey there!"}
            </h1>
            <p className="text-sm text-muted">What would you like to start?</p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {sections.map((section) => {
              const active =
                !!section.createKind && selected === section.createKind;
              return (
                <div
                  key={section.mode}
                  className={`flex flex-col gap-4 rounded-xl border p-5 transition-colors ${
                    active
                      ? section.color.active
                      : `border-border bg-surface/60 ${section.color.hover}`
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className={`flex h-10 w-10 items-center justify-center rounded-lg ${section.color.icon}`}
                    >
                      {section.icon}
                    </span>
                    <span className="text-sm font-medium">{section.title}</span>
                  </div>

                  <p className="flex-1 text-[12px] leading-relaxed text-muted">
                    {section.description}
                  </p>

                  <div className="flex items-center gap-2">
                    {section.createKind && (
                      <button
                        type="button"
                        onClick={() =>
                          setSelected((cur) =>
                            cur === section.createKind
                              ? null
                              : section.createKind ?? null,
                          )
                        }
                        aria-pressed={active}
                        className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${section.color.primary}`}
                      >
                        {active ? <X size={15} /> : <Plus size={15} />}
                        <span className="whitespace-nowrap">
                          {active ? "Close" : section.newLabel}
                        </span>
                      </button>
                    )}
                    {onNavigate && (
                      <button
                        type="button"
                        onClick={() => onNavigate(section.mode)}
                        className={`group flex items-center justify-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-muted transition-colors hover:bg-surface hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                          section.createKind ? "" : "flex-1"
                        }`}
                      >
                        <span className="whitespace-nowrap">
                          {section.createKind ? "Open" : section.openLabel}
                        </span>
                        <ArrowRight
                          size={15}
                          className={`transition-transform group-hover:translate-x-0.5 ${section.color.accent}`}
                        />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* The picked start surface renders on the same page, under the cards.
          The shared hero/form surfaces vertically center by default as full-pane
          empty states; top-align them here so they sit right below the cards. */}
      {selected && (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="[&>*]:!h-auto [&>*]:!justify-start [&>*]:!py-6">
            {surfaces[selected]}
          </div>
        </div>
      )}

      {/* Persona + settings, pinned to the bottom-left to mirror the sidebar
          footer. `mt-auto` keeps it at the bottom when no surface is open. */}
      {(onOpenSettings || onOpenArchive) && (
        <div className="mt-auto shrink-0 border-t border-border px-3 py-2">
          <div className="w-56 max-w-full">
            <PersonaMenu
              onOpenSettings={onOpenSettings ?? (() => {})}
              onOpenArchive={onOpenArchive ?? (() => {})}
            />
          </div>
        </div>
      )}
    </div>
  );
}
