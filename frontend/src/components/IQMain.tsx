import { useEffect, useRef, useState } from "react";
import type { ComponentType, FormEvent, MouseEvent } from "react";
import {
  AlignLeft,
  ArrowRight,
  ArrowUp,
  AudioLines,
  Bot,
  Lightbulb,
  Loader2,
  MessageSquare,
  MessagesSquare,
  NotebookText,
  Paperclip,
  Radio,
  Sparkles,
  StickyNote,
  Type,
  User,
} from "lucide-react";
import { api } from "../lib/api";
import { CITATION_HREF, linkedCitations, navigableHit, toSearchResult } from "../lib/iq";
import type { IQNavHit } from "../lib/iq";
import { sectionColor } from "../lib/sections";
import type { IQAskResponse, IQField, IQHit, SearchResult, SearchSection } from "../lib/types";
import { Markdown } from "./Markdown";

type Icon = ComponentType<{ size?: number; className?: string }>;

const SECTION_ICON: Record<SearchSection, Icon> = {
  topics: MessagesSquare,
  chats: MessageSquare,
  live: Radio,
  agents: Bot,
};

const SECTION_LABEL: Record<SearchSection, string> = {
  topics: "Topic",
  chats: "Chat",
  live: "Live",
  agents: "Agent",
};

const FIELD_META: Record<IQField, { label: string; icon: Icon }> = {
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

// Questions that make sense in any workspace, to show what IQ is for.
const EXAMPLES = [
  "What decisions did we make recently?",
  "Which action items are still open, and who owns them?",
  "What are the main risks I should know about?",
];

// Must match the number of passages the backend grounds an answer on, so the
// early results and the answer's sources share their numbering.
const SOURCE_LIMIT = 8;

/** A question handed in by the ⌘⇧K shortcut; `nonce` changes on every press. */
export interface IQSeed {
  question: string;
  nonce: number;
}

interface Props {
  /** Open the item a source belongs to (topic, chat, agent, live session). */
  onOpenResult: (result: SearchResult, query: string) => void;
  seed?: IQSeed | null;
}

/**
 * The IQ section: a single, centred question box over Precursor IQ. Asking
 * lists the matching content straight away (each row opens the real item),
 * then adds the cited answer once the model has written it. Nothing is kept:
 * this is a way to find things, not a conversation.
 */
export function IQMain({ onOpenResult, seed = null }: Props) {
  const [draft, setDraft] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const [hits, setHits] = useState<IQHit[] | null>(null);
  const [answer, setAnswer] = useState<IQAskResponse | null>(null);
  const [answering, setAnswering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Guards against a slow reply landing after a newer question was asked.
  const requestId = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Pre-fill (not ask) so the selected text can be edited into a question.
  const selectOnRender = useRef(false);
  useEffect(() => {
    if (!seed) return;
    selectOnRender.current = true;
    if (seed.question) setDraft(seed.question);
    // Keyed on the nonce: each press re-applies, even with the same text.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.nonce]);
  // Runs once the seeded draft has rendered, so the selection spans it. With
  // an empty seed (nothing was selected) the current draft is selected as is.
  useEffect(() => {
    if (!selectOnRender.current) return;
    if (seed?.question && inputRef.current?.value !== seed.question) return;
    selectOnRender.current = false;
    inputRef.current?.focus();
    inputRef.current?.select();
  });

  async function ask(question: string): Promise<void> {
    const q = question.trim();
    if (!q) return;
    const id = ++requestId.current;
    setDraft(q);
    setAsked(q);
    setHits(null);
    setAnswer(null);
    setError(null);
    setAnswering(true);
    // Matching content is fast; show it while the model writes the answer.
    void api.iq
      .retrieve(q, [], SOURCE_LIMIT)
      .then((r) => {
        if (id === requestId.current) setHits((prev) => prev ?? r.hits);
      })
      .catch(() => {});
    try {
      const resp = await api.iq.ask(q);
      if (id !== requestId.current) return;
      setAnswer(resp);
      setHits(resp.sources);
    } catch (e) {
      if (id !== requestId.current) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === requestId.current) setAnswering(false);
    }
  }

  function onSubmit(e: FormEvent): void {
    e.preventDefault();
    void ask(draft);
  }

  function open(hit: IQNavHit): void {
    // No highlight term: a question isn't a phrase that appears in the item.
    onOpenResult(toSearchResult(hit), "");
  }

  function openNumber(n: number): void {
    const hit = hits?.find((h) => h.id === n);
    const nav = hit ? navigableHit(hit) : null;
    if (nav) open(nav);
  }

  // Citations are rendered as `#iq-source-n` links; open their source instead
  // of following the fragment.
  function onAnswerClick(e: MouseEvent<HTMLDivElement>): void {
    const link = (e.target as HTMLElement).closest("a");
    const href = link?.getAttribute("href") ?? "";
    if (!href.startsWith(CITATION_HREF)) return;
    e.preventDefault();
    openNumber(Number(href.slice(CITATION_HREF.length)));
  }

  const cited = new Set(answer?.citations.map((c) => c.id) ?? []);
  const directMatches = (hits ?? [])
    .filter((h) => h.is_title)
    .map((h) => navigableHit(h))
    .filter((h): h is IQNavHit => h !== null);
  const idle = asked === null;

  return (
    <div className="h-full overflow-y-auto">
      <div
        className={`mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 md:px-8 ${
          idle ? "min-h-full justify-center py-10" : "py-8"
        }`}
      >
        {idle && (
          <div className="flex flex-col items-center gap-3 text-center">
            <span
              className={`flex h-12 w-12 items-center justify-center rounded-xl ${sectionColor("iq").icon}`}
            >
              <Sparkles size={24} />
            </span>
            <h1 className="text-2xl font-semibold tracking-tight">Ask Precursor IQ</h1>
            <p className="max-w-md text-sm text-muted">
              Ask about anything in your topics, chats, agents and live sessions. The answer
              cites its sources, and each one opens the real item.
            </p>
          </div>
        )}

        <form onSubmit={onSubmit} className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-border bg-surface/60 px-3 focus-within:border-accent">
            <Sparkles size={16} className="shrink-0 text-muted" />
            <input
              ref={inputRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask a question about your workspace…"
              aria-label="Question for Precursor IQ"
              className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted"
              autoFocus
              autoComplete="off"
            />
          </div>
          <button
            type="submit"
            disabled={!draft.trim() || (answering && draft.trim() === asked)}
            aria-label="Ask"
            data-tooltip="Ask"
            className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {answering ? <Loader2 size={18} className="animate-spin" /> : <ArrowUp size={18} />}
          </button>
        </form>

        {idle && (
          <div className="flex flex-wrap justify-center gap-2">
            {EXAMPLES.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => void ask(q)}
                className="rounded-full border border-border px-3 py-1.5 text-xs text-muted hover:border-accent/50 hover:text-text"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {directMatches.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted">
              Go to
            </span>
            {directMatches.map((hit) => {
              const Icon = SECTION_ICON[hit.section];
              const color = sectionColor(hit.section);
              return (
                <button
                  key={`${hit.section}-${hit.entity_id}`}
                  type="button"
                  onClick={() => open(hit)}
                  className={`flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-sm ${color.hoverCard}`}
                >
                  <Icon size={14} className={color.accentText} />
                  {hit.title}
                  <ArrowRight size={12} className="text-muted" />
                </button>
              );
            })}
          </div>
        )}

        {!idle && (
          <section className="rounded-xl border border-border bg-surface/40 p-4">
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted">
              <Sparkles size={12} className="text-accent" />
              Answer
              {answer?.model && (
                <span className="ml-auto font-normal normal-case tracking-normal">
                  {answer.model}
                </span>
              )}
            </div>
            {answering && (
              <div className="flex items-center gap-2 py-2 text-sm text-muted">
                <Loader2 size={14} className="animate-spin" />
                Reading your workspace…
              </div>
            )}
            {error && (
              <div className="rounded border border-red-500/50 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-400">
                {error}
              </div>
            )}
            {answer && (
              <div onClick={onAnswerClick}>
                <Markdown className="text-sm">{linkedCitations(answer.answer)}</Markdown>
              </div>
            )}
          </section>
        )}

        {hits && hits.length > 0 && (
          <section className="flex flex-col gap-1">
            <h2 className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted">
              {answer ? "Sources" : "Matching content"}
            </h2>
            <ul className="flex flex-col gap-1">
              {hits.map((hit) => (
                <SourceRow
                  key={hit.id}
                  hit={hit}
                  cited={cited.has(hit.id)}
                  onOpen={open}
                />
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function SourceRow({
  hit,
  cited,
  onOpen,
}: {
  hit: IQHit;
  cited: boolean;
  onOpen: (hit: IQNavHit) => void;
}) {
  const nav = navigableHit(hit);
  const meta = FIELD_META[hit.field];
  const FieldIcon = meta.icon;
  const tint = nav ? sectionColor(nav.section) : null;
  const body = (
    <>
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-xs font-semibold ${
          tint ? tint.icon : "bg-surface text-muted"
        }`}
      >
        {hit.id}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium">{hit.title}</span>
          <span
            className={`shrink-0 text-[10px] font-medium uppercase tracking-wide ${
              tint ? tint.accentText : "text-muted"
            }`}
          >
            {nav ? SECTION_LABEL[nav.section] : "Memory"}
          </span>
          {cited && (
            <span className="shrink-0 rounded bg-accent/10 px-1 text-[10px] font-medium text-accent">
              cited
            </span>
          )}
        </span>
        {!hit.is_title && hit.snippet && hit.snippet !== hit.title && (
          <span className="truncate text-[11px] text-muted">
            {hit.role && (
              <span className="mr-1 font-medium capitalize text-muted/80">{hit.role}:</span>
            )}
            {hit.snippet}
          </span>
        )}
      </span>
      <span
        className={`ml-auto flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
          tint ? tint.icon : "bg-surface text-muted"
        }`}
      >
        <FieldIcon size={11} />
        {meta.label}
      </span>
    </>
  );
  if (!nav) {
    return (
      <li
        className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2"
        data-tooltip="Memory entries have no page to open."
      >
        {body}
      </li>
    );
  }
  return (
    <li>
      <button
        type="button"
        id={`iq-source-${hit.id}`}
        onClick={() => onOpen(nav)}
        className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-surface"
      >
        {body}
      </button>
    </li>
  );
}

/** The IQ section's sidebar body: IQ has no list, so it explains itself. */
export function IQSidebar() {
  return (
    <div className="flex flex-col gap-2 px-3 py-3 text-xs text-muted">
      <p>
        Ask a question in plain words. Precursor IQ searches your topics, briefs, messages,
        attachments, chats, agents, live sessions and memory, then answers with numbered
        sources.
      </p>
      <p>
        Click a source to open it. Questions and answers aren't saved. Press ⌘⇧K from
        anywhere to come back here, with any selected text as the question.
      </p>
    </div>
  );
}
