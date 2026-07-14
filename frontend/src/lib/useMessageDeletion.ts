import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { TIMING } from "./constants";
import type { Message } from "./types";

interface PendingDelete {
  message: Message;
  timer: number;
}

export interface UseMessageDeletionOptions {
  /** Identity of the owning conversation; a change flushes queued deletions. */
  resetKey: number;
  /** Commit a delete server-side (topic vs chat endpoint). */
  deleteMessage: (messageId: number) => Promise<unknown>;
  /** Remove the message from the local persisted transcript. */
  setPersisted: Dispatch<SetStateAction<Message[]>>;
}

export interface MessageDeletion {
  pendingDeletes: PendingDelete[];
  /** Ids currently hidden pending a delete-grace timeout. */
  hiddenIds: Set<number>;
  requestDeleteMessage: (message: Message) => void;
  undoDelete: (messageId: number) => void;
}

/**
 * Soft-delete with an undo grace window, shared by both conversation panels.
 * A requested delete hides the message and arms a timer; if not undone, it is
 * committed server-side. Queued deletes are flushed on unmount / conversation
 * switch. Only the `deleteMessage` endpoint differs between topics and chats.
 */
export function useMessageDeletion({
  resetKey,
  deleteMessage,
  setPersisted,
}: UseMessageDeletionOptions): MessageDeletion {
  const [pendingDeletes, setPendingDeletes] = useState<PendingDelete[]>([]);
  const pendingDeletesRef = useRef<PendingDelete[]>([]);

  useEffect(() => {
    pendingDeletesRef.current = pendingDeletes;
  }, [pendingDeletes]);

  // Flush queued deletions on unmount / conversation switch: cancel the grace
  // timer and commit each delete immediately so the next visit is consistent.
  useEffect(() => {
    return () => {
      const queued = pendingDeletesRef.current;
      pendingDeletesRef.current = [];
      for (const p of queued) {
        window.clearTimeout(p.timer);
        void Promise.resolve(deleteMessage(p.message.id)).catch(() => {});
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  function commitDelete(messageId: number): void {
    setPendingDeletes((prev) => prev.filter((p) => p.message.id !== messageId));
    setPersisted((prev) => prev.filter((m) => m.id !== messageId));
    void Promise.resolve(deleteMessage(messageId)).catch(() => {});
  }

  function requestDeleteMessage(message: Message): void {
    if (pendingDeletesRef.current.some((p) => p.message.id === message.id)) return;
    const timer = window.setTimeout(() => commitDelete(message.id), TIMING.UNDO_DELETE_MS);
    setPendingDeletes((prev) => [...prev, { message, timer }]);
  }

  function undoDelete(messageId: number): void {
    setPendingDeletes((prev) => {
      const hit = prev.find((p) => p.message.id === messageId);
      if (hit) window.clearTimeout(hit.timer);
      return prev.filter((p) => p.message.id !== messageId);
    });
  }

  const hiddenIds = useMemo(
    () => new Set(pendingDeletes.map((p) => p.message.id)),
    [pendingDeletes],
  );

  return { pendingDeletes, hiddenIds, requestDeleteMessage, undoDelete };
}
