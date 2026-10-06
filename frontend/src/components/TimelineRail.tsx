import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { CornerDownRight, History, Pencil } from "lucide-react";
import { formatTimestamp } from "./MessageMeta";
import { Z_INDEX } from "../lib/constants";
import { MIN_TIMELINE_TURNS } from "../lib/timeline";
import { rewindOffered, type RewindController } from "../lib/useRewind";
import type { TurnTimeline } from "../lib/useTurnTimeline";

/** A run of consecutive turns the rail sets apart, e.g. an agent's executions. */
export interface TurnGroup {
  key: string | number;
  label: string;
}

interface TimelineRailProps {
  timeline: TurnTimeline;
  rewind: RewindController;
  streaming: boolean;
  /** Groups turns: a gap separates groups and the preview card names the group. */
  group?: (index: number) => TurnGroup | null;
}

/**
 * The transcript's turn timeline: one dash per turn (a prompt and its reply),
 * grouped and centred on the right edge of the transcript. Dashes on screen are
 * brighter and the turn being read is accent-coloured. Hover or focus a dash for
 * a preview card that jumps to the turn or starts a rewind there.
 */
export function TimelineRail({ timeline, rewind, streaming, group }: TimelineRailProps) {
  const { turns, view } = timeline;
  const navRef = useRef<HTMLElement>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [focusIdx, setFocusIdx] = useState(0);
  const closeTimer = useRef<number | null>(null);

  useEffect(() => {
    if (open !== null && open >= turns.length) setOpen(null);
    if (focusIdx >= turns.length) setFocusIdx(Math.max(0, turns.length - 1));
  }, [turns.length, open, focusIdx]);
  useEffect(() => () => cancelClose(), []);

  const preview = rewind.preview;
  const keptThrough = preview ? (preview.mode === "edit" ? preview.index - 1 : preview.index) : null;

  function cancelClose(): void {
    if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    closeTimer.current = null;
  }
  function scheduleClose(): void {
    cancelClose();
    closeTimer.current = window.setTimeout(() => {
      if (!navRef.current?.contains(document.activeElement)) setOpen(null);
    }, 150);
  }
  function tickEl(i: number): HTMLButtonElement | null {
    return navRef.current?.querySelector<HTMLButtonElement>(`[data-tick="${i}"]`) ?? null;
  }
  function act(fn: () => void): void {
    setOpen(null);
    fn();
  }
  const offered = (i: number, mode: "rewind" | "edit") =>
    !streaming && !rewind.busy && rewindOffered(rewind, i, turns.length, mode);
  function startRewind(i: number, mode: "rewind" | "edit"): void {
    act(() => {
      rewind.start(turns, i, mode);
      void timeline.jumpTo(i);
    });
  }

  function onKeyDown(e: KeyboardEvent<HTMLElement>): void {
    if (!(e.target instanceof HTMLButtonElement) || e.target.dataset.tick === undefined) return;
    const i = Number(e.target.dataset.tick);
    const move = (to: number) => {
      e.preventDefault();
      const next = Math.max(0, Math.min(turns.length - 1, to));
      setFocusIdx(next);
      tickEl(next)?.focus();
    };
    if (e.key === "ArrowDown") move(i + 1);
    else if (e.key === "ArrowUp") move(i - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(turns.length - 1);
    else if (e.key === "Escape") setOpen(null);
    else if (e.key.toLowerCase() === "r" && offered(i, "rewind")) {
      e.preventDefault();
      startRewind(i, "rewind");
    } else if (e.key.toLowerCase() === "e" && offered(i, "edit")) {
      e.preventDefault();
      startRewind(i, "edit");
    }
  }

  const card = open !== null ? turns[open] : null;
  const tick = open !== null ? tickEl(open) : null;
  const cardTop = tick ? tick.offsetTop + tick.offsetHeight / 2 : 0;

  if (turns.length < MIN_TIMELINE_TURNS) return null;
  return (
    <nav
      ref={navRef}
      aria-label="Conversation timeline"
      aria-keyshortcuts="ArrowUp ArrowDown R E"
      className={`absolute inset-y-3 right-4 flex w-6 flex-col items-center justify-center ${Z_INDEX.RAISED}`}
      onMouseLeave={scheduleClose}
      onMouseEnter={cancelClose}
      onKeyDown={onKeyDown}
      onBlur={(e) => {
        if (!navRef.current?.contains(e.relatedTarget as Node | null)) setOpen(null);
      }}
    >
      {turns.map((t, i) => {
        const doomed = keptThrough !== null && i > keptThrough;
        const cut = keptThrough !== null && i === keptThrough;
        const inView = i >= view.first && i <= view.last;
        const current = i === view.current;
        let bar = "scale-x-[.45] scale-y-[.67] bg-muted opacity-50";
        if (inView) bar = "scale-x-[.67] scale-y-[.67] bg-text opacity-100";
        if (current) bar = "scale-x-90 bg-accent opacity-100";
        if (t.has_compaction && !current) bar = bar.replace(/bg-\S+/, "bg-amber-500");
        if (doomed) bar = "scale-x-[.67] scale-y-[.67] bg-red-500 opacity-70";
        if (cut) bar = "scale-x-110 bg-amber-500 opacity-100";
        const startsGroup = group != null && i > 0 && group(i)?.key !== group(i - 1)?.key;
        return (
          <Fragment key={t.message_id}>
            {startsGroup && <span aria-hidden className="my-1 block h-px w-2.5 shrink-0 bg-border" />}
            <button
              type="button"
              data-tick={i}
              tabIndex={i === focusIdx ? 0 : -1}
              aria-label={`Turn ${i + 1} of ${turns.length}: ${t.prompt || "(attachment)"}`}
              aria-current={current ? "step" : undefined}
              onMouseEnter={() => {
                cancelClose();
                setOpen(i);
              }}
              onFocus={() => {
                setFocusIdx(i);
                setOpen(i);
              }}
              onClick={() => act(() => void timeline.jumpTo(i))}
              className="group flex min-h-0.5 w-6 shrink basis-2.5 cursor-pointer items-center justify-center outline-none"
            >
              <span
                className={`block h-[3px] w-[18px] rounded-full transition-[transform,opacity,background-color] duration-150 group-hover:scale-100 group-hover:bg-accent group-hover:opacity-100 group-focus-visible:scale-100 group-focus-visible:bg-accent group-focus-visible:opacity-100 ${bar}`}
              />
            </button>
          </Fragment>
        );
      })}
      {card && open !== null && (
        <div
          className="absolute right-full pr-2"
          style={{ top: cardTop, transform: "translateY(-50%)" }}
          onMouseEnter={cancelClose}
        >
          <div className="w-80 rounded-lg border border-border bg-surface p-3 text-xs shadow-xl">
            <div className="mb-1 flex justify-between gap-2 text-[11px] text-muted">
              <span>
                Turn {open + 1} of {turns.length}
                {open < turns.length - 1 && ` · ${turns.length - 1 - open} later`}
              </span>
              <span>{formatTimestamp(card.created_at)}</span>
            </div>
            {group?.(open) && (
              <p className="mb-1 text-[11px] font-medium text-muted">{group(open)?.label}</p>
            )}
            <p className="line-clamp-2 font-medium text-text">{card.prompt || "(attachment)"}</p>
            {card.reply && <p className="mt-1 line-clamp-2 text-muted">{card.reply}</p>}
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => act(() => void timeline.jumpTo(open))}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:bg-bg"
              >
                <CornerDownRight size={12} aria-hidden /> Jump
              </button>
              <button
                type="button"
                disabled={!offered(open, "rewind")}
                onClick={() => startRewind(open, "rewind")}
                data-tooltip="Keep this turn and drop the later ones (R)"
                className="inline-flex items-center gap-1 rounded-md border border-amber-500/60 bg-amber-500/15 px-2 py-1 text-amber-800 hover:bg-amber-500/25 disabled:cursor-not-allowed disabled:opacity-40 dark:text-amber-200"
              >
                <History size={12} aria-hidden /> Rewind here
              </button>
              <button
                type="button"
                disabled={!offered(open, "edit")}
                onClick={() => startRewind(open, "edit")}
                data-tooltip="Drop this turn and the later ones, and edit its prompt (E)"
                className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 hover:bg-bg disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Pencil size={12} aria-hidden /> Edit &amp; resend
              </button>
            </div>
            {rewind.canRewind && !rewind.canRewind(open, "edit") && (
              <p className="mt-2 text-[11px] text-muted">
                Navigation only: this turn belongs to an earlier session or a workflow-driven run,
                or was recorded before rewind was available.
              </p>
            )}
          </div>
        </div>
      )}
    </nav>
  );
}
