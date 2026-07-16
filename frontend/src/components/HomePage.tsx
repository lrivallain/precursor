import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Bot,
  FolderGit2,
  MessageSquare,
  MessageSquarePlus,
  MessagesSquare,
  Radio,
  Sparkles,
  SquareKanban,
} from "lucide-react";
import { api } from "../lib/api";
import type { SidebarMode } from "./Sidebar";
import type { Me } from "../lib/types";

type HomeKind = "topics" | "chats" | "live" | "agents";

interface LauncherCard {
  kind: HomeKind;
  title: string;
  description: string;
  icon: ReactNode;
}

interface NavLink {
  mode: SidebarMode;
  label: string;
  icon: ReactNode;
}

interface Props {
  /** Inline start surfaces, shown on the same page under the cards. */
  topicSurface: ReactNode;
  chatSurface: ReactNode;
  liveSurface: ReactNode;
  agentSurface: ReactNode;
  liveEnabled?: boolean;
  /** Whether the Kanban section is available (adds a "Jump to" nav link). */
  kanbanEnabled?: boolean;
  /** Jump straight into a section's list/surface (leaves the home launcher). */
  onNavigate?: (mode: SidebarMode) => void;
}

/**
 * Landing surface shown at `/`. A greeting and the launcher cards (topic, chat,
 * live session, agent), pinned to the top. Picking a card reveals that section's
 * start surface right below on the same page — no redirect, and the cards stay
 * put. Picking it again hides the surface.
 *
 * The sidebar is hidden at home, so a top nav row lets the user jump directly
 * into any existing section.
 */
export function HomePage({
  topicSurface,
  chatSurface,
  liveSurface,
  agentSurface,
  liveEnabled = true,
  kanbanEnabled = false,
  onNavigate,
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

  const cards: LauncherCard[] = [
    {
      kind: "topics",
      title: "New topic",
      description:
        "A long-lived thread that keeps its own history, context, and optional linked issue.",
      icon: <MessagesSquare size={20} />,
    },
    {
      kind: "chats",
      title: "New chat",
      description:
        "A quick, throwaway conversation. Type a prompt and get going in seconds.",
      icon: <MessageSquarePlus size={20} />,
    },
    ...(liveEnabled
      ? [
          {
            kind: "live",
            title: "New live session",
            description:
              "Capture a meeting live with transcription, notes, and summaries as it happens.",
            icon: <Radio size={20} />,
          } satisfies LauncherCard,
        ]
      : []),
    {
      kind: "agents",
      title: "New agent",
      description:
        "Hand a task to an autonomous coding agent and follow its progress.",
      icon: <Bot size={20} />,
    },
  ];

  const surfaces: Record<HomeKind, ReactNode> = {
    topics: topicSurface,
    chats: chatSurface,
    live: liveSurface,
    agents: agentSurface,
  };

  const navLinks: NavLink[] = [
    { mode: "topics", label: "Topics", icon: <MessagesSquare size={14} /> },
    { mode: "chats", label: "Chats", icon: <MessageSquare size={14} /> },
    ...(liveEnabled
      ? [{ mode: "live", label: "Live", icon: <Radio size={14} /> } satisfies NavLink]
      : []),
    { mode: "workspaces", label: "Files", icon: <FolderGit2 size={14} /> },
    { mode: "agents", label: "Agents", icon: <Bot size={14} /> },
    ...(kanbanEnabled
      ? [
          {
            mode: "kanban",
            label: "Kanban",
            icon: <SquareKanban size={14} />,
          } satisfies NavLink,
        ]
      : []),
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex w-full shrink-0 flex-col">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
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

          {onNavigate && (
            <nav
              aria-label="Sections"
              className="flex flex-wrap items-center gap-1.5"
            >
              <span className="mr-1 text-xs font-medium text-muted">
                Jump to
              </span>
              {navLinks.map((link) => (
                <button
                  key={link.mode}
                  type="button"
                  onClick={() => onNavigate(link.mode)}
                  className="flex items-center gap-1.5 rounded-full border border-border bg-surface/60 px-3 py-1.5 text-sm text-muted transition-colors hover:border-accent/50 hover:bg-surface hover:text-text focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {link.icon}
                  <span className="whitespace-nowrap">{link.label}</span>
                </button>
              ))}
            </nav>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {cards.map((card) => {
              const active = selected === card.kind;
              return (
                <button
                  key={card.kind}
                  type="button"
                  onClick={() =>
                    setSelected((cur) => (cur === card.kind ? null : card.kind))
                  }
                  aria-pressed={active}
                  className={`group flex flex-col gap-3 rounded-xl border p-5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                    active
                      ? "border-accent/60 bg-accent/10"
                      : "border-border bg-surface/60 hover:border-accent/50 hover:bg-surface"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent">
                      {card.icon}
                    </span>
                    <ArrowRight
                      size={18}
                      className={`transition-transform ${
                        active
                          ? "text-accent"
                          : "text-muted group-hover:translate-x-0.5 group-hover:text-accent"
                      }`}
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <span className="text-sm font-medium">{card.title}</span>
                    <span className="text-[12px] leading-relaxed text-muted">
                      {card.description}
                    </span>
                  </div>
                </button>
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
    </div>
  );
}
