import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, ReactNode } from "react";
import {
  AlignLeft,
  AudioLines,
  Bot,
  FolderGit2,
  Gauge,
  Home,
  Lightbulb,
  MessageSquare,
  MessagesSquare,
  Radio,
  Search,
  Sparkles,
  SquareKanban,
  StickyNote,
  Type,
  User,
} from "lucide-react";
import type { SidebarMode } from "./Sidebar";
import { SECTION_COLORS } from "../lib/sections";
import { Modal } from "./Modal";
import { api } from "../lib/api";
import type { SearchField, SearchResult, SearchSection } from "../lib/types";

/**
 * A single jump target in the palette. `mode` drives the icon tint (via
 * SECTION_COLORS) and is undefined for the Home target, which has its own
 * neutral accent.
 */
interface PaletteItem {
  id: string;
  label: string;
  hint: string;
  /** Extra searchable terms so a query can match beyond the visible label. */
  keywords: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  /** Section tint key; omitted for Home. */
  mode?: SidebarMode;
  run: () => void;
}

interface Props {
  onClose: () => void;
  /** Jump to a section (leaves the home launcher). */
  onNavigate: (mode: SidebarMode) => void;
  /** Jump to the home launcher. */
  onGoHome: () => void;
  /** Open a specific content hit (topic/chat/agent/live session). */
  onOpenResult: (result: SearchResult, query: string) => void;
  liveEnabled?: boolean;
  kanbanEnabled?: boolean;
  /**
   * Seed the input with the ongoing search term (from `?q=`) so reopening the
   * palette after picking a hit continues the same search instead of starting
   * blank.
   */
  initialQuery?: string;
}

// Section → icon for a content hit's left badge (tinted via SECTION_COLORS).
const SECTION_ICON: Record<SearchSection, ComponentType<{ size?: number; className?: string }>> = {
  topics: MessagesSquare,
  chats: MessageSquare,
  live: Radio,
  agents: Bot,
};

// Which field matched → badge label + icon. Title hits are grouped separately;
// the icon still disambiguates the origin of a hit at a glance.
const FIELD_META: Record<
  SearchField,
  { label: string; icon: ComponentType<{ size?: number; className?: string }> }
> = {
  title: { label: "Title", icon: Type },
  description: { label: "Description", icon: AlignLeft },
  message: { label: "Message", icon: MessageSquare },
  prompt: { label: "Prompt", icon: User },
  answer: { label: "Answer", icon: Sparkles },
  transcript: { label: "Transcript", icon: AudioLines },
  insight: { label: "Insight", icon: Lightbulb },
  notes: { label: "Notes", icon: StickyNote },
  summary: { label: "Summary", icon: Sparkles },
};

// Human labels for the section chip on a content hit.
const SECTION_LABEL: Record<SearchSection, string> = {
  topics: "Topic",
  chats: "Chat",
  live: "Live",
  agents: "Agent",
};

/**
 * Split `text` around every (case-insensitive) occurrence of `query`, wrapping
 * matches in a tinted <mark> so the palette shows *where* a hit matched. `cls`
 * carries the section's colour so the highlight reads in that section's hue.
 */
function highlight(text: string, query: string, cls: string): ReactNode {
  const q = query.trim();
  if (!q) return text;
  const lower = text.toLowerCase();
  const lq = q.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    const idx = lower.indexOf(lq, i);
    if (idx < 0) {
      out.push(text.slice(i));
      break;
    }
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark key={key++} className={`rounded-[3px] px-0.5 ${cls}`}>
        {text.slice(idx, idx + q.length)}
      </mark>,
    );
    i = idx + q.length;
  }
  return out;
}

/**
 * Keyboard-first launcher **and** content search. Opened with ⌘K / Ctrl+K.
 *
 * With an empty query it's the section switcher (type to filter, ↑/↓ to move,
 * Enter to jump, a digit to pick that numbered row). Once you type, it also
 * searches across topics, chats, agents (prompts + final answers) and live
 * sessions: title/name hits are floated above discussion hits, and every hit is
 * badged with its section colour/icon and the field that matched.
 */
export function CommandPalette({
  onClose,
  onNavigate,
  onGoHome,
  onOpenResult,
  liveEnabled = true,
  kanbanEnabled = false,
  initialQuery = "",
}: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [active, setActive] = useState(0);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const items = useMemo<PaletteItem[]>(() => {
    const nav = (mode: SidebarMode) => () => {
      onNavigate(mode);
      onClose();
    };
    const all: PaletteItem[] = [
      {
        id: "home",
        label: "Home",
        hint: "Landing launcher",
        keywords: "home start launcher overview",
        icon: Home,
        run: () => {
          onGoHome();
          onClose();
        },
      },
      {
        id: "topics",
        label: "Topics",
        hint: "Long-lived threads",
        keywords: "topics threads conversations history",
        icon: MessagesSquare,
        mode: "topics",
        run: nav("topics"),
      },
      {
        id: "chats",
        label: "Chats",
        hint: "Quick conversations",
        keywords: "chats quick throwaway prompt",
        icon: MessageSquare,
        mode: "chats",
        run: nav("chats"),
      },
      ...(liveEnabled
        ? [
            {
              id: "live",
              label: "Live",
              hint: "Meeting capture",
              keywords: "live meeting transcription notes session",
              icon: Radio,
              mode: "live" as const,
              run: nav("live"),
            },
          ]
        : []),
      {
        id: "workspaces",
        label: "Files",
        hint: "Workspaces & files",
        keywords: "files workspaces folders code",
        icon: FolderGit2,
        mode: "workspaces",
        run: nav("workspaces"),
      },
      {
        id: "agents",
        label: "Agents",
        hint: "Autonomous coding agents",
        keywords: "agents autonomous coding tasks",
        icon: Bot,
        mode: "agents",
        run: nav("agents"),
      },
      ...(kanbanEnabled
        ? [
            {
              id: "kanban",
              label: "Kanban",
              hint: "Issue board",
              keywords: "kanban board issues tracking",
              icon: SquareKanban,
              mode: "kanban" as const,
              run: nav("kanban"),
            },
          ]
        : []),
      {
        id: "cockpits",
        label: "Cockpits",
        hint: "Local web apps & URLs",
        keywords: "cockpits dashboards webapps localhost url embed iframe",
        icon: Gauge,
        mode: "cockpits",
        run: nav("cockpits"),
      },
    ];
    return all;
  }, [liveEnabled, kanbanEnabled, onNavigate, onGoHome, onClose]);

  const sections = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (it) => it.label.toLowerCase().includes(q) || it.keywords.includes(q),
    );
  }, [items, query]);

  // Debounced content search. Empty query clears results; a stale in-flight
  // request is ignored via the `cancelled` guard.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const resp = await api.search.query(q);
        if (!cancelled) setResults(resp.results);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [query]);

  // Title hits float above discussion hits (the backend already sorts this way,
  // but we split them into labelled groups to make the priority explicit).
  const titleHits = useMemo(() => results.filter((r) => r.is_title), [results]);
  const bodyHits = useMemo(() => results.filter((r) => !r.is_title), [results]);

  // Flat activation order across every interactive row: sections, then title
  // hits, then discussion hits. Keeps ↑/↓/Enter working over the whole list.
  const rowRuns = useMemo<(() => void)[]>(() => {
    const runs: (() => void)[] = sections.map((it) => it.run);
    const open = (r: SearchResult) => () => {
      onOpenResult(r, query);
      onClose();
    };
    for (const r of titleHits) runs.push(open(r));
    for (const r of bodyHits) runs.push(open(r));
    return runs;
  }, [sections, titleHits, bodyHits, onOpenResult, onClose, query]);

  const sectionCount = sections.length;
  const titleBase = sectionCount;
  const bodyBase = sectionCount + titleHits.length;

  // Clamp the active row whenever the combined set shrinks.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, rowRuns.length - 1)));
  }, [rowRuns.length]);

  // Reset to the top on every new query so the first (best) hit is preselected.
  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    // Preselect a seeded term so the user can refine (type over) or extend it
    // without manually clearing first.
    if (initialQuery) el.select();
  }, []);

  // Keep the highlighted row scrolled into view for long, filtered lists.
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-row="${active}"]`);
    (el as HTMLElement | null)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function onKeyDown(e: React.KeyboardEvent): void {
    const n = rowRuns.length;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (n ? (a + 1) % n : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (n ? (a - 1 + n) % n : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      rowRuns[active]?.();
    } else if (/^[0-9]$/.test(e.key) && !query) {
      // Digit shortcuts jump straight to the Nth section (0-indexed) when not
      // mid-search.
      const idx = Number(e.key);
      if (sections[idx]) {
        e.preventDefault();
        rowRuns[idx]?.();
      }
    }
  }

  const hasQuery = query.trim().length > 0;
  const nothingFound =
    hasQuery && !searching && sections.length === 0 && results.length === 0;

  function resultRow(r: SearchResult, index: number): ReactNode {
    const isActive = index === active;
    const tint = SECTION_COLORS[r.section].icon;
    const accent = SECTION_COLORS[r.section].accentText;
    const SectionIcon = SECTION_ICON[r.section];
    const meta = FIELD_META[r.field];
    const FieldIcon = meta.icon;
    // Title hits show the (highlighted) title as the primary line; body hits
    // show the title plainly with the highlighted snippet beneath it.
    return (
      <li key={`${r.section}-${r.entity_id}-${r.field}-${index}`}>
        <button
          type="button"
          data-row={index}
          onMouseMove={() => setActive(index)}
          onClick={() => rowRuns[index]?.()}
          className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left ${
            isActive ? "bg-surface" : "hover:bg-surface/60"
          }`}
        >
          <span
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${tint}`}
          >
            <SectionIcon size={16} />
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="flex items-center gap-1.5">
              <span className="truncate text-sm font-medium">
                {r.is_title ? highlight(r.title, query, tint) : r.title}
              </span>
              <span
                className={`shrink-0 text-[10px] font-medium uppercase tracking-wide ${accent}`}
              >
                {SECTION_LABEL[r.section]}
              </span>
            </span>
            {!r.is_title && r.snippet && (
              <span className="truncate text-[11px] text-muted">
                {r.role && (
                  <span className="mr-1 font-medium capitalize text-muted/80">
                    {r.role}:
                  </span>
                )}
                {highlight(r.snippet, query, tint)}
              </span>
            )}
          </span>
          <span
            className={`ml-auto flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${tint}`}
            title={`Matched in ${meta.label.toLowerCase()}`}
          >
            <FieldIcon size={11} />
            {meta.label}
          </span>
        </button>
      </li>
    );
  }

  return (
    <Modal
      onClose={onClose}
      closeOnEscape
      closeOnBackdrop
      padded
      backdropClassName="bg-black/40"
      panelClassName="bg-bg border border-border rounded-xl shadow-2xl flex flex-col w-full overflow-hidden"
      panelStyle={{ maxWidth: 560, maxHeight: "72vh" }}
      labelledBy="command-palette-input"
    >
      <div className="flex items-center gap-2 border-b border-border px-3 h-12 shrink-0">
        <Search size={16} className="text-muted shrink-0" />
        <input
          id="command-palette-input"
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search topics, chats, agents, live… or jump to a section"
          className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
          autoComplete="off"
          spellCheck={false}
        />
        {searching && (
          <span className="shrink-0 text-[10px] text-muted">Searching…</span>
        )}
      </div>

      <ul ref={listRef} className="min-h-0 flex-1 overflow-y-auto p-1.5">
        {nothingFound && (
          <li className="px-3 py-6 text-center text-sm text-muted">
            No matches for “{query.trim()}”.
          </li>
        )}

        {/* Sections — jump targets (the original palette behaviour). */}
        {sections.length > 0 && (
          <>
            {hasQuery && (
              <li className="px-2 pt-1 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
                Go to
              </li>
            )}
            {sections.map((it, i) => {
              const isActive = i === active;
              const tint = it.mode
                ? SECTION_COLORS[it.mode].icon
                : "bg-accent/10 text-accent";
              return (
                <li key={it.id}>
                  <button
                    type="button"
                    data-row={i}
                    onMouseMove={() => setActive(i)}
                    onClick={() => rowRuns[i]?.()}
                    className={`flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left ${
                      isActive ? "bg-surface" : "hover:bg-surface/60"
                    }`}
                  >
                    <span
                      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${tint}`}
                    >
                      <it.icon size={16} />
                    </span>
                    <span className="flex min-w-0 flex-col">
                      <span className="truncate text-sm font-medium">{it.label}</span>
                      <span className="truncate text-[11px] text-muted">{it.hint}</span>
                    </span>
                    {!query && i < 10 && (
                      <kbd className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted">
                        {i}
                      </kbd>
                    )}
                  </button>
                </li>
              );
            })}
          </>
        )}

        {/* Title matches — floated above discussion hits. */}
        {titleHits.length > 0 && (
          <>
            <li className="px-2 pt-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
              Titles
            </li>
            {titleHits.map((r, i) => resultRow(r, titleBase + i))}
          </>
        )}

        {/* Discussion / body matches. */}
        {bodyHits.length > 0 && (
          <>
            <li className="px-2 pt-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
              In content
            </li>
            {bodyHits.map((r, i) => resultRow(r, bodyBase + i))}
          </>
        )}
      </ul>

      <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[10px] text-muted shrink-0">
        <span>↑↓ Navigate</span>
        <span>↵ Open</span>
        {!hasQuery && <span>0–9 Jump</span>}
        <span className="ml-auto">Esc Close</span>
      </div>
    </Modal>
  );
}
