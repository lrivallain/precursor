import { useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, ReactNode } from "react";
import {
  AlertCircle,
  AlignLeft,
  AudioLines,
  BookOpen,
  Bot,
  FolderGit2,
  Home,
  Lightbulb,
  Loader2,
  Paperclip,
  NotebookText,
  MessageSquare,
  MessagesSquare,
  Radio,
  Search,
  Sparkles,
  StickyNote,
  Type,
  User,
  Workflow,
} from "lucide-react";
import type { SidebarMode } from "./Sidebar";
import { sectionColor } from "../lib/sections";
import type { SectionPlugin } from "../lib/plugins";
import { Modal } from "./Modal";
import { Markdown } from "./Markdown";
import { api } from "../lib/api";
import type {
  AgentSession,
  IQAskResponse,
  IQField,
  IQHit,
  SearchField,
  SearchResult,
  SearchSection,
} from "../lib/types";
import { agentNeedsAttention, sortAgentsByUrgency } from "../lib/agents";

/**
 * A single jump target in the palette. `mode` drives the icon tint (via
 * sectionColor) and is undefined for the Home target, which has its own
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
  /**
   * Live agent sessions, so the palette can float agents that need the human
   * (urgency-first) as quick-jump rows above the section switcher (idea 6).
   */
  agents?: AgentSession[];
  /** Jump straight to a specific agent session. */
  onOpenAgent?: (id: number) => void;
  liveEnabled?: boolean;
  /** Plugin-contributed sections, listed after core's own. */
  pluginSections?: SectionPlugin[];
  /**
   * Seed the input with the ongoing search term (from `?q=`) so reopening the
   * palette after picking a hit continues the same search instead of starting
   * blank.
   */
  initialQuery?: string;
}

// Section → icon for a content hit's left badge (tinted via sectionColor).
const SECTION_ICON: Record<SearchSection, ComponentType<{ size?: number; className?: string }>> = {
  topics: MessagesSquare,
  chats: MessageSquare,
  live: Radio,
  agents: Bot,
};

/**
 * A content hit as the palette renders it. Precursor IQ hits carry a few more
 * field kinds than the legacy substring search (briefs, attachments), and the
 * palette never shows memory, so the section stays a navigable one.
 */
interface PaletteHit {
  section: SearchSection;
  field: IQField;
  is_title: boolean;
  entity_id: number;
  ref: string | null;
  title: string;
  snippet: string;
  role: string | null;
  updated_at: string | null;
}

// Entity kinds the palette can open (memory has no page to jump to).
const PALETTE_SECTIONS: SearchSection[] = ["topics", "chats", "agents", "live"];

function fromIQ(hit: IQHit): PaletteHit | null {
  if (hit.section === "memory") return null;
  return { ...hit, section: hit.section };
}

// The opener only cares about section + entity; fold IQ-only fields onto the
// nearest legacy one so the shared `SearchResult` shape still holds.
function toSearchResult(hit: PaletteHit): SearchResult {
  const field: SearchField =
    hit.field === "brief"
      ? "summary"
      : hit.field === "attachment" || hit.field === "memory"
        ? "message"
        : hit.field;
  return { ...hit, field };
}

// Which field matched → badge label + icon. Title hits are grouped separately;
// the icon still disambiguates the origin of a hit at a glance.
const FIELD_META: Record<
  IQField,
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
  brief: { label: "Brief", icon: NotebookText },
  attachment: { label: "Attachment", icon: Paperclip },
  memory: { label: "Memory", icon: StickyNote },
};

// Words worth highlighting: the query's terms, not the whole phrase, since IQ
// matches them independently and in any order.
function queryTerms(query: string): string[] {
  return Array.from(
    new Set(
      query
        .toLowerCase()
        .split(/[^\p{L}\p{N}_]+/u)
        .filter((w) => w.length >= 2),
    ),
  ).sort((a, b) => b.length - a.length);
}

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
  const terms = queryTerms(query);
  if (!terms.length) return text;
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    let idx = -1;
    let len = 0;
    for (const t of terms) {
      const at = lower.indexOf(t, i);
      if (at >= 0 && (idx < 0 || at < idx)) {
        idx = at;
        len = t.length;
      }
    }
    if (idx < 0) {
      out.push(text.slice(i));
      break;
    }
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark key={key++} className={`rounded-[3px] px-0.5 ${cls}`}>
        {text.slice(idx, idx + len)}
      </mark>,
    );
    i = idx + len;
  }
  return out;
}

// Answers cite sources as `[^n]`; show them as plain `[n]` markers that line
// up with the numbered source list under the answer.
function plainCitations(answer: string): string {
  return answer.replace(/\[\^(\d+)\]/g, "[$1]");
}

/**
 * Keyboard-first launcher **and** content search. Opened with ⌘K / Ctrl+K, or
 * with a bare `/` when focus isn't in a text field.
 *
 * With an empty query it's the section switcher (type to filter, ↑/↓ to move,
 * Enter to jump, a digit to pick that numbered row). Once you type, it also
 * searches across topics, chats, agents (prompts + final answers) and live
 * sessions with Precursor IQ's relevance ranking: title/name hits are floated
 * above content hits, and every hit is badged with its section colour/icon and
 * the field that matched. Tab (or the "Ask Precursor" row) switches to Ask
 * mode: a cited answer written from the same index.
 */
export function CommandPalette({
  onClose,
  onNavigate,
  onGoHome,
  onOpenResult,
  agents = [],
  onOpenAgent,
  liveEnabled = true,
  pluginSections = [],
  initialQuery = "",
}: Props) {
  const [query, setQuery] = useState(initialQuery);
  const [active, setActive] = useState(0);
  const [results, setResults] = useState<PaletteHit[]>([]);
  const [searching, setSearching] = useState(false);
  // Ask mode: the question the answer is for, the answer, and request state.
  const [askedFor, setAskedFor] = useState<string | null>(null);
  const [answer, setAnswer] = useState<IQAskResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  // Precursor IQ can be turned off server-side; the palette then keeps the
  // plain substring search and hides Ask.
  const [iqAvailable, setIqAvailable] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  // The scroll container: the result list, or the answer panel in Ask mode.
  const listRef = useRef<HTMLElement | null>(null);

  const items = useMemo<PaletteItem[]>(() => {
    const nav = (mode: SidebarMode) => () => {
      onNavigate(mode);
      onClose();
    };
    // Attention router: agents blocked/failed/interrupted float to the very top
    // of the palette, urgency-first, so ⌘K is also a triage queue.
    const attention: PaletteItem[] = onOpenAgent
      ? sortAgentsByUrgency(agents.filter(agentNeedsAttention)).map((a) => ({
          id: `agent-attn-${a.id}`,
          label: a.title,
          hint:
            a.status === "needs_approval"
              ? a.pending_permission?.title
                ? `Waiting: ${a.pending_permission.title}`
                : "Waiting for approval"
              : a.status === "failed"
                ? "Failed — needs attention"
                : "Interrupted",
          keywords: "agent waiting approval attention blocked failed",
          icon: AlertCircle,
          mode: "agents" as const,
          run: () => {
            onOpenAgent(a.id);
            onClose();
          },
        }))
      : [];
    const all: PaletteItem[] = [
      ...attention,
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
      {
        id: "workflows",
        label: "Workflows",
        hint: "Chain agents into pipelines",
        keywords: "workflows pipelines automation orchestration sequence agents",
        icon: Workflow,
        mode: "workflows",
        run: nav("workflows"),
      },
      ...pluginSections.map((plugin) => ({
        id: plugin.id,
        label: plugin.label,
        hint: plugin.description,
        keywords: `${plugin.label} ${plugin.keywords ?? ""}`.toLowerCase(),
        icon: plugin.icon,
        mode: plugin.id,
        run: nav(plugin.id),
      })),
      {
        id: "docs",
        label: "Documentation",
        hint: "Open the docs (new tab)",
        keywords: "docs documentation help guide manual website",
        icon: BookOpen,
        run: () => {
          window.open("/docs/", "_blank", "noopener,noreferrer");
          onClose();
        },
      },
    ];
    return all;
  }, [liveEnabled, pluginSections, onNavigate, onGoHome, onClose, agents, onOpenAgent]);

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
        let hits: PaletteHit[];
        try {
          const resp = await api.iq.retrieve(q, PALETTE_SECTIONS, 30);
          hits = resp.hits.map(fromIQ).filter((h): h is PaletteHit => h !== null);
          if (!cancelled) setIqAvailable(true);
        } catch {
          // IQ disabled (404) or failing: the substring search still works.
          const resp = await api.search.query(q);
          hits = resp.results;
          if (!cancelled) setIqAvailable(false);
        }
        if (!cancelled) setResults(hits);
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

  // Title hits float above content hits (ranked by relevance within each group).
  const titleHits = useMemo(() => results.filter((r) => r.is_title), [results]);
  const bodyHits = useMemo(() => results.filter((r) => !r.is_title), [results]);

  const inAskMode = askedFor !== null;
  // Sources keep their citation number so the list lines up with the answer.
  const answerSources = useMemo<{ hit: PaletteHit; n: number }[]>(
    () =>
      (answer?.sources ?? []).flatMap((source) => {
        const hit = fromIQ(source);
        return hit ? [{ hit, n: source.id }] : [];
      }),
    [answer],
  );

  async function runAsk(): Promise<void> {
    const q = query.trim();
    if (!q || !iqAvailable) return;
    setAskedFor(q);
    setAnswer(null);
    setAskError(null);
    setAsking(true);
    setActive(0);
    try {
      const resp = await api.iq.ask(q);
      setAnswer(resp);
    } catch (e) {
      setAskError(e instanceof Error ? e.message : String(e));
    } finally {
      setAsking(false);
    }
  }

  // Flat activation order across every interactive row: sections, then title
  // hits, then content hits, then "Ask Precursor". In Ask mode, the answer's
  // sources are the rows. Keeps ↑/↓/Enter working over the whole list.
  const rowRuns = useMemo<(() => void)[]>(() => {
    const open = (r: PaletteHit) => () => {
      onOpenResult(toSearchResult(r), inAskMode ? "" : query);
      onClose();
    };
    if (inAskMode) return answerSources.map(({ hit }) => open(hit));
    const runs: (() => void)[] = sections.map((it) => it.run);
    for (const r of titleHits) runs.push(open(r));
    for (const r of bodyHits) runs.push(open(r));
    if (query.trim() && iqAvailable) runs.push(() => void runAsk());
    return runs;
    // runAsk reads the current query/iqAvailable, both already listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    sections,
    titleHits,
    bodyHits,
    onOpenResult,
    onClose,
    query,
    inAskMode,
    answerSources,
    iqAvailable,
  ]);

  const sectionCount = sections.length;
  const titleBase = sectionCount;
  const bodyBase = sectionCount + titleHits.length;
  const askRow = bodyBase + bodyHits.length;

  // Clamp the active row whenever the combined set shrinks.
  useEffect(() => {
    setActive((a) => Math.min(a, Math.max(0, rowRuns.length - 1)));
  }, [rowRuns.length]);

  // Reset to the top on every new query so the first (best) hit is preselected.
  // Editing the query also leaves Ask mode: the answer was for the old text.
  useEffect(() => {
    setActive(0);
    setAskedFor(null);
    setAnswer(null);
    setAskError(null);
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
    } else if (e.key === "Tab" && query.trim() && iqAvailable && !inAskMode) {
      e.preventDefault();
      void runAsk();
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
    hasQuery &&
    !searching &&
    !inAskMode &&
    sections.length === 0 &&
    results.length === 0;

  function resultRow(r: PaletteHit, index: number, citation?: number): ReactNode {
    const isActive = index === active;
    const tint = sectionColor(r.section).icon;
    const accent = sectionColor(r.section).accentText;
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
            {citation !== undefined ? (
              <span className="text-xs font-semibold">{citation}</span>
            ) : (
              <SectionIcon size={16} />
            )}
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
            data-tooltip={`Matched in ${meta.label.toLowerCase()}`}
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
        {searching && !inAskMode && (
          <span className="shrink-0 text-[10px] text-muted">Searching…</span>
        )}
        {hasQuery && iqAvailable && !inAskMode && (
          <button
            type="button"
            onClick={() => void runAsk()}
            className="flex shrink-0 items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted hover:text-text"
            data-tooltip="Answer from your topics, chats, agents and live notes (Tab)"
          >
            <Sparkles size={11} />
            Ask
          </button>
        )}
      </div>

      {inAskMode ? (
        <div
          ref={(el) => {
            listRef.current = el;
          }}
          className="min-h-0 flex-1 overflow-y-auto p-3"
        >
          <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
            <Sparkles size={12} className="text-accent" />
            Precursor answer
            {answer?.model && (
              <span className="ml-auto font-normal normal-case tracking-normal">
                {answer.model}
              </span>
            )}
          </div>
          {asking && (
            <div className="flex items-center gap-2 px-1 py-4 text-sm text-muted">
              <Loader2 size={14} className="animate-spin" />
              Reading your workspace…
            </div>
          )}
          {askError && (
            <div className="rounded border border-red-500/50 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
              {askError}
            </div>
          )}
          {answer && (
            <Markdown className="text-sm">{plainCitations(answer.answer)}</Markdown>
          )}
          {answerSources.length > 0 && (
            <ul className="mt-3 border-t border-border pt-2">
              <li className="px-2 pb-0.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
                Sources
              </li>
              {answerSources.map(({ hit, n }, i) => resultRow(hit, i, n))}
            </ul>
          )}
        </div>
      ) : (
      <ul
        ref={(el) => {
          listRef.current = el;
        }}
        className="min-h-0 flex-1 overflow-y-auto p-1.5"
      >
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
                ? sectionColor(it.mode).icon
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

        {/* Hand the query to Precursor IQ for a cited answer. */}
        {hasQuery && iqAvailable && (
          <li>
            <button
              type="button"
              data-row={askRow}
              onMouseMove={() => setActive(askRow)}
              onClick={() => rowRuns[askRow]?.()}
              className={`mt-1 flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left ${
                active === askRow ? "bg-surface" : "hover:bg-surface/60"
              }`}
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/10 text-accent">
                <Sparkles size={16} />
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-sm font-medium">
                  Ask Precursor: “{query.trim()}”
                </span>
                <span className="truncate text-[11px] text-muted">
                  A cited answer from your topics, chats, agents and live notes
                </span>
              </span>
              <kbd className="ml-auto shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] font-medium text-muted">
                Tab
              </kbd>
            </button>
          </li>
        )}
      </ul>
      )}

      <div className="flex items-center gap-3 border-t border-border px-3 py-1.5 text-[10px] text-muted shrink-0">
        <span>↑↓ Navigate</span>
        <span>↵ Open</span>
        {!hasQuery && <span>0–9 Jump</span>}
        {hasQuery && iqAvailable && !inAskMode && <span>⇥ Ask</span>}
        <span className="ml-auto">Esc Close</span>
      </div>
    </Modal>
  );
}
