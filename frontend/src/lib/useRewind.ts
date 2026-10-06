import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import { TIMING } from "./constants";
import { rewindCut, type RewindMode } from "./timeline";
import type { Message, TurnIndexItem } from "./types";

export interface RewindPreview {
  /** 0-based index of the turn the rewind was asked on. */
  index: number;
  mode: RewindMode;
  fromId: number;
  droppedTurns: number;
}

export interface PendingRewind {
  fromId: number;
  /** Newest row the user saw when confirming; later rows survive the cut. */
  throughId: number;
  droppedTurns: number;
  mode: RewindMode;
  /** Epoch ms at which the rewind commits, so a countdown tracks the real timer. */
  expiresAt: number;
}

export interface UseRewindOptions {
  /** Identity of the owning conversation; a change commits a queued rewind. */
  resetKey: string;
  rewind: (fromMessageId: number, throughMessageId: number) => Promise<unknown>;
  setPersisted: Dispatch<SetStateAction<Message[]>>;
  persistedRef: MutableRefObject<Message[]>;
  /** The composer draft, to hand an edited prompt back to the user. */
  draft: string;
  setDraft: (text: string) => void;
  /** A reply is generating: rewinding is refused server-side meanwhile. */
  streaming: boolean;
  /** Run after a rewind lands so the transcript and sidebar resync. */
  onSettled: () => void;
  onError: (message: string) => void;
}

export interface RewindController {
  /** The cut awaiting confirmation; rows from `fromId` on render as doomed. */
  preview: RewindPreview | null;
  /** A confirmed cut inside its undo grace window. */
  pending: PendingRewind | null;
  /** Ids hidden by a pending rewind (inclusive range), or null. */
  hidden: { fromId: number; throughId: number } | null;
  start: (turns: readonly TurnIndexItem[], index: number, mode: RewindMode) => void;
  cancel: () => void;
  confirm: () => void;
  undo: () => void;
  /**
   * Commit a pending rewind now, or wait for one already committing. Resolves
   * false when it failed: a new prompt must not land on the un-rewound history.
   */
  flush: () => Promise<boolean>;
  /** Whether `mode` can be offered on turn `index`; absent means every turn can. */
  canRewind?: (index: number, mode: RewindMode) => boolean;
  /** Confirming is final: no undo grace (agent sessions, where the SDK can't undo). */
  irreversible?: boolean;
  /** A confirmed rewind is being applied. */
  busy?: boolean;
}

/** Whether `rewind` offers `mode` on turn `index` of `count`. */
export function rewindOffered(
  rewind: RewindController,
  index: number,
  count: number,
  mode: RewindMode,
): boolean {
  if (mode === "rewind" && index >= count - 1) return false;
  return rewind.canRewind ? rewind.canRewind(index, mode) : true;
}

/** Whether a persisted row falls inside a rewind's cut. */
export function inCut(id: number, cut: { fromId: number; throughId: number } | null): boolean {
  return cut !== null && id >= cut.fromId && id <= cut.throughId;
}

/**
 * Return a conversation to an earlier turn. A rewind goes preview → confirm →
 * undo grace → commit, like a message delete: confirmed rows are hidden at once
 * and only deleted server-side once the grace window passes, or as soon as the
 * user sends a new prompt (which must land after the cut, not inside it).
 */
export function useRewind({
  resetKey,
  rewind,
  setPersisted,
  persistedRef,
  draft,
  setDraft,
  streaming,
  onSettled,
  onError,
}: UseRewindOptions): RewindController {
  const [preview, setPreview] = useState<RewindPreview | null>(null);
  const [pending, setPending] = useState<PendingRewind | null>(null);
  const pendingRef = useRef<{ cut: PendingRewind; timer: number } | null>(null);
  const inflightRef = useRef<Promise<boolean> | null>(null);
  // The draft an "edit" replaced, and the prompt it put there, for undo.
  const editRef = useRef<{ before: string; prefill: string } | null>(null);
  const rewindRef = useRef(rewind);
  rewindRef.current = rewind;

  // Commit a queued rewind when leaving the conversation so the next visit
  // matches what the user saw; drop any unconfirmed preview.
  useEffect(() => {
    return () => {
      const queued = pendingRef.current;
      pendingRef.current = null;
      editRef.current = null;
      setPreview(null);
      setPending(null);
      if (queued) {
        window.clearTimeout(queued.timer);
        void Promise.resolve(rewindRef.current(queued.cut.fromId, queued.cut.throughId)).catch(
          () => {},
        );
      }
    };
  }, [resetKey]);

  // A reply that starts mid-preview (e.g. a scheduled run) changes the tail
  // the preview was computed on.
  useEffect(() => {
    if (streaming) setPreview(null);
  }, [streaming]);

  function commit(): Promise<boolean> {
    const queued = pendingRef.current;
    if (!queued) return inflightRef.current ?? Promise.resolve(true);
    window.clearTimeout(queued.timer);
    pendingRef.current = null;
    editRef.current = null;
    const run = (async () => {
      try {
        // Rows stay hidden until the server has dropped them, so nothing that
        // refetches on the transcript change can read the pre-rewind state.
        await rewindRef.current(queued.cut.fromId, queued.cut.throughId);
        setPersisted((prev) => prev.filter((m) => !inCut(m.id, queued.cut)));
        onSettled();
        return true;
      } catch (err) {
        // Nothing was deleted: un-hiding the rows restores the transcript.
        onError(`Rewind failed: ${(err as Error).message}`);
        return false;
      } finally {
        inflightRef.current = null;
        setPending(null);
      }
    })();
    inflightRef.current = run;
    return run;
  }

  function start(turns: readonly TurnIndexItem[], index: number, mode: RewindMode): void {
    if (streaming || pendingRef.current) return;
    const cut = rewindCut(turns, index, mode);
    if (!cut) return;
    setPreview({ index, mode, ...cut });
  }

  function confirm(): void {
    if (!preview || streaming) return;
    setPreview(null);
    if (preview.mode === "edit") {
      const prompt = persistedRef.current.find((m) => m.id === preview.fromId)?.content ?? "";
      editRef.current = { before: draft, prefill: prompt };
      setDraft(prompt);
    }
    const throughId = persistedRef.current.reduce((max, m) => Math.max(max, m.id), 0);
    const cut: PendingRewind = {
      fromId: preview.fromId,
      throughId,
      droppedTurns: preview.droppedTurns,
      mode: preview.mode,
      expiresAt: Date.now() + TIMING.UNDO_REWIND_MS,
    };
    const timer = window.setTimeout(() => void commit(), TIMING.UNDO_REWIND_MS);
    pendingRef.current = { cut, timer };
    setPending(cut);
  }

  function undo(): void {
    const queued = pendingRef.current;
    if (!queued) return;
    window.clearTimeout(queued.timer);
    pendingRef.current = null;
    setPending(null);
    // Only put the old draft back if the user hasn't started editing the prompt.
    const edit = editRef.current;
    editRef.current = null;
    if (edit && draft === edit.prefill) setDraft(edit.before);
  }

  const hidden = useMemo(
    () => (pending ? { fromId: pending.fromId, throughId: pending.throughId } : null),
    [pending],
  );

  return {
    preview,
    pending,
    hidden,
    start,
    cancel: () => setPreview(null),
    confirm,
    undo,
    flush: commit,
  };
}
