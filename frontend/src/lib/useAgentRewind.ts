import { useCallback, useEffect, useState } from "react";
import type { RewindMode } from "./timeline";
import type { RewindController, RewindPreview } from "./useRewind";
import type { TurnIndexItem } from "./types";

/**
 * A cockpit exchange (a human prompt, its work and its answer) as a timeline
 * turn. `message_id` holds the exchange index, which is what the prompt's
 * `TURN_ANCHOR_ATTR` carries.
 */
export interface AgentTurn extends TurnIndexItem {
  /** SDK id of the prompt when this turn can be rewound, else null. */
  event_id: string | null;
  /** The whole prompt, handed back to the composer by Edit & resend. */
  text: string;
}

export interface UseAgentRewindOptions {
  /** Identity of the transcript on screen; a change drops a preview. */
  resetKey: string;
  turns: readonly AgentTurn[];
  /** A turn is in flight: the server refuses a rewind meanwhile. */
  streaming: boolean;
  rewind: (eventId: string) => Promise<unknown>;
  setDraft: (text: string) => void;
  /** Run after a rewind lands so the transcript and sidebar resync. */
  onSettled: () => void;
  onError: (message: string) => void;
}

/**
 * Rewind for an agent session: preview → explicit confirmation → done. Unlike
 * topics and chats there is no undo grace, because the SDK can't reverse a
 * rewind; the confirmation says so instead.
 */
export function useAgentRewind({
  resetKey,
  turns,
  streaming,
  rewind,
  setDraft,
  onSettled,
  onError,
}: UseAgentRewindOptions): RewindController {
  const [preview, setPreview] = useState<RewindPreview | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setPreview(null), [resetKey]);
  useEffect(() => {
    if (streaming) setPreview(null);
  }, [streaming]);

  // Only the first dropped prompt has to be rewindable: the SDK drops what
  // follows it, and only the current run's prompts carry an id.
  const canRewind = useCallback(
    (index: number, mode: RewindMode) => {
      const first = mode === "edit" ? index : index + 1;
      return index >= 0 && first < turns.length && turns[first].event_id != null;
    },
    [turns],
  );

  function start(_turns: readonly TurnIndexItem[], index: number, mode: RewindMode): void {
    if (streaming || busy || !canRewind(index, mode)) return;
    const first = mode === "edit" ? index : index + 1;
    setPreview({
      index,
      mode,
      fromId: turns[first].message_id,
      droppedTurns: turns.length - first,
    });
  }

  async function apply(cut: RewindPreview): Promise<void> {
    const turn = turns.find((t) => t.message_id === cut.fromId);
    if (!turn?.event_id) {
      setPreview(null);
      return;
    }
    setBusy(true);
    try {
      await rewind(turn.event_id);
      if (cut.mode === "edit") setDraft(turn.text);
      onSettled();
    } catch (err) {
      onError(`Rewind failed: ${(err as Error).message}`);
    } finally {
      setPreview(null);
      setBusy(false);
    }
  }

  return {
    preview,
    pending: null,
    hidden: null,
    start,
    cancel: () => {
      if (!busy) setPreview(null);
    },
    confirm: () => {
      if (preview && !streaming && !busy) void apply(preview);
    },
    undo: () => {},
    flush: () => Promise.resolve(true),
    canRewind,
    irreversible: true,
    busy,
  };
}
