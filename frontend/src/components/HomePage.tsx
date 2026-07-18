import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  ArrowRight,
  Bot,
  FolderGit2,
  MessageSquarePlus,
  MessagesSquare,
  Radio,
  SquareKanban,
} from "lucide-react";
import { api } from "../lib/api";
import type { SidebarMode } from "./Sidebar";
import type { Me } from "../lib/types";
import { SECTION_COLORS } from "../lib/sections";
import { PersonaMenu } from "./PersonaMenu";

type HomeKind = "topics" | "chats" | "live" | "agents";

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

/** Fold/unroll duration for the start-surface panel; mirrors the CSS transition. */
const REVEAL_MS = 400;

/**
 * Landing surface shown at `/`. A greeting and a grid of section cards. A single
 * click reveals that section's start surface right below (or, for sections with
 * no wizard, jumps straight into the section); a double click opens the section.
 * Every section carries its own color scheme to make the grid easy to scan.
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
  // Cross-fade between wizards: `shown` is the surface currently mounted and
  // `open` drives its unroll. When `selected` (the target) diverges we fold the
  // current panel closed, swap `shown` once it's collapsed, then unroll the new
  // one — so switching sections reads as a smooth fold-out → fold-in rather than
  // an instant content swap.
  const [shown, setShown] = useState<HomeKind | null>(null);
  const [open, setOpen] = useState(false);
  const swapTimer = useRef<number | null>(null);
  // Distinguish a single click (show wizard / open) from a double click (open
  // section): a single click's action is deferred by a short, fixed window and
  // cancelled when a second click on the same card lands, so the two gestures
  // don't both fire. We do our own detection instead of the native dblclick
  // event, whose threshold can be long enough to feel laggy.
  const clickTimer = useRef<number | null>(null);
  const pendingMode = useRef<SidebarMode | null>(null);

  useEffect(
    () => () => {
      if (clickTimer.current) window.clearTimeout(clickTimer.current);
      if (swapTimer.current) window.clearTimeout(swapTimer.current);
    },
    [],
  );

  // Fold the visible panel closed before swapping in the target surface. When
  // nothing is showing yet, mount the target straight away (it opens below).
  useEffect(() => {
    if (selected === shown) return;
    if (shown === null) {
      setShown(selected);
      return;
    }
    setOpen(false);
    const reduce = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (swapTimer.current) window.clearTimeout(swapTimer.current);
    swapTimer.current = window.setTimeout(
      () => setShown(selected),
      reduce ? 0 : REVEAL_MS,
    );
    return () => {
      if (swapTimer.current) window.clearTimeout(swapTimer.current);
    };
  }, [selected, shown]);

  // Once the target surface is mounted, unroll it open on the next frame so the
  // grid-rows transition runs from collapsed → full.
  useEffect(() => {
    if (shown !== null && shown === selected) {
      const raf = requestAnimationFrame(() => setOpen(true));
      return () => cancelAnimationFrame(raf);
    }
    if (shown === null) setOpen(false);
  }, [shown, selected]);


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

  function clearPending() {
    if (clickTimer.current) window.clearTimeout(clickTimer.current);
    clickTimer.current = null;
    pendingMode.current = null;
  }

  /** Single-click action: reveal the wizard (or open a wizard-less section). */
  function activate(section: Section) {
    if (section.createKind) {
      const kind = section.createKind;
      setSelected((cur) => (cur === kind ? null : kind));
    } else {
      onNavigate?.(section.mode);
    }
  }

  function openSection(section: Section) {
    clearPending();
    onNavigate?.(section.mode);
  }

  // Distinguish single from double click ourselves with a 250ms window: a
  // second click on the same card within the window opens the section; a lone
  // click resolves to `activate` once the window elapses.
  function handleCardClick(section: Section) {
    if (clickTimer.current && pendingMode.current === section.mode) {
      clearPending();
      onNavigate?.(section.mode);
      return;
    }
    clearPending();
    pendingMode.current = section.mode;
    clickTimer.current = window.setTimeout(() => {
      clickTimer.current = null;
      pendingMode.current = null;
      activate(section);
    }, 250);
  }

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
    },
    {
      mode: "workspaces",
      title: "Files",
      description:
        "Browse the workspaces and files backing your sessions.",
      openLabel: "Browse files",
      icon: <FolderGit2 size={20} />,
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
            <div className="flex items-center gap-2.5 text-accent">
              <img
                src="/logo.svg"
                alt=""
                aria-hidden="true"
                width={32}
                height={32}
                className="rounded-md"
              />
              <span className="text-sm font-semibold uppercase tracking-wide">
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
              const color = SECTION_COLORS[section.mode];
              return (
                <div
                  key={section.mode}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleCardClick(section)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      activate(section);
                    }
                  }}
                  aria-pressed={active}
                  aria-label={
                    section.createKind ? section.newLabel : section.openLabel
                  }
                  className={`flex cursor-pointer flex-col gap-4 rounded-xl border p-5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                    active
                      ? color.activeCard
                      : `border-border bg-surface/60 ${color.hoverCard}`
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span
                      className={`flex h-10 w-10 items-center justify-center rounded-lg ${color.icon}`}
                    >
                      {section.icon}
                    </span>
                    <span className="text-sm font-medium">{section.title}</span>
                  </div>

                  <p className="flex-1 text-[12px] leading-relaxed text-muted">
                    {section.description}
                  </p>

                  <div className="flex items-center justify-end">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        openSection(section);
                      }}
                      className={`group flex items-center gap-1 rounded text-[11px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-accent ${color.accentText}`}
                    >
                      <span className="whitespace-nowrap">
                        {section.openLabel}
                      </span>
                      <ArrowRight
                        size={13}
                        className="transition-transform group-hover:translate-x-0.5"
                      />
                    </button>
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
      {shown && (
        <div className={`min-h-0 flex-1 overflow-y-auto section-${shown}`}>
          <div className="mx-auto w-full max-w-2xl px-8 pb-8">
            <div className={`wizard-reveal${open ? " is-open" : ""}`}>
              <div className="wizard-panel [&>*]:!h-auto [&>*]:!justify-start [&>*]:!py-6">
                {surfaces[shown]}
              </div>
            </div>
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
