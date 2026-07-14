import { useCallback, useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { Message } from "./types";
import { PAGINATION } from "./constants";

export interface WindowedMessagesOptions {
  /**
   * Fetch a page of persisted messages. Omitting `beforeId` requests the most
   * recent page; passing it requests the page immediately older than that id.
   */
  fetchPage: (opts: { limit: number; beforeId?: number }) => Promise<Message[]>;
  /** Messages per page. Defaults to the shared PAGINATION.MESSAGE_PAGE_SIZE. */
  pageSize?: number;
}

export interface WindowedMessages {
  persisted: Message[];
  setPersisted: Dispatch<SetStateAction<Message[]>>;
  /** Mirrors `persisted` for async callbacks that need the latest snapshot. */
  persistedRef: React.MutableRefObject<Message[]>;
  hasMoreOlder: boolean;
  loadingOlder: boolean;
  setHasMoreOlder: Dispatch<SetStateAction<boolean>>;
  /** Stable callback to feed `useChatScroll` as its onReachTop handler. */
  onReachTop: () => void;
  /**
   * Wire the scroll helpers once `useChatScroll` has produced them. Call in an
   * effect; the helpers are stable so this only needs to run when they change.
   */
  bindScroll: (helpers: { captureTopAnchor: () => void; pinToBottom: () => void }) => void;
  /**
   * Fetch the most recent page without touching state. Pair with
   * `applyFirstPage` so the caller can insert its own cancellation guard
   * between the fetch and the state update (conversation switches race).
   */
  fetchFirstPage: () => Promise<Message[]>;
  /** Pin to the bottom and replace the window with a freshly fetched page. */
  applyFirstPage: (msgs: Message[]) => void;
  /**
   * Reload the most recent slice while preserving roughly the open window, so a
   * post-stream / post-command refresh doesn't collapse to a single page.
   * Returns the applied rows, or null on failure.
   */
  reloadMessages: () => Promise<Message[] | null>;
}

/**
 * Reverse-infinite-scroll windowing for a chat transcript: owns the persisted
 * message window, the older-page loader, and the flag/ref plumbing that both
 * conversation panels previously duplicated. The only per-surface delta is the
 * `fetchPage` closure (topic vs chat), injected by the caller.
 */
export function useWindowedMessages({
  fetchPage,
  pageSize = PAGINATION.MESSAGE_PAGE_SIZE,
}: WindowedMessagesOptions): WindowedMessages {
  const [persisted, setPersisted] = useState<Message[]>([]);
  const persistedRef = useRef<Message[]>([]);
  const [hasMoreOlder, setHasMoreOlder] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const hasMoreOlderRef = useRef(false);
  const loadingOlderRef = useRef(false);

  useEffect(() => {
    persistedRef.current = persisted;
  }, [persisted]);
  useEffect(() => {
    hasMoreOlderRef.current = hasMoreOlder;
  }, [hasMoreOlder]);

  // Scroll helpers are produced by useChatScroll, which is created after this
  // hook (it needs the persisted window). Read them through refs so the loaders
  // stay stable and the reach-top wiring doesn't re-subscribe.
  const captureTopAnchorRef = useRef<() => void>(() => {});
  const pinToBottomRef = useRef<() => void>(() => {});
  const bindScroll = useCallback(
    (helpers: { captureTopAnchor: () => void; pinToBottom: () => void }) => {
      captureTopAnchorRef.current = helpers.captureTopAnchor;
      pinToBottomRef.current = helpers.pinToBottom;
    },
    [],
  );

  const loadOlder = useCallback(async (): Promise<void> => {
    if (loadingOlderRef.current || !hasMoreOlderRef.current) return;
    const oldest = persistedRef.current[0];
    if (!oldest || oldest.id <= 0) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    try {
      const older = await fetchPage({ limit: pageSize, beforeId: oldest.id });
      if (older.length === 0) {
        setHasMoreOlder(false);
        return;
      }
      captureTopAnchorRef.current();
      setPersisted((prev) => {
        const seen = new Set(prev.map((m) => m.id));
        const fresh = older.filter((m) => !seen.has(m.id));
        return fresh.length ? [...fresh, ...prev] : prev;
      });
      setHasMoreOlder(older.length >= pageSize);
    } catch {
      // Keep what we have; a transient failure shouldn't drop the transcript.
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, [fetchPage, pageSize]);

  const loadOlderRef = useRef<() => void>(() => {});
  const onReachTop = useCallback(() => loadOlderRef.current(), []);
  useEffect(() => {
    loadOlderRef.current = () => void loadOlder();
  }, [loadOlder]);

  const fetchFirstPage = useCallback(
    (): Promise<Message[]> => fetchPage({ limit: pageSize }).catch(() => [] as Message[]),
    [fetchPage, pageSize],
  );

  const applyFirstPage = useCallback(
    (msgs: Message[]): void => {
      pinToBottomRef.current();
      setPersisted(msgs);
      setHasMoreOlder(msgs.length >= pageSize);
    },
    [pageSize],
  );

  const reloadMessages = useCallback(async (): Promise<Message[] | null> => {
    const want = Math.max(pageSize, persistedRef.current.length + 10);
    try {
      const msgs = await fetchPage({ limit: want });
      setPersisted(msgs);
      setHasMoreOlder(msgs.length >= want);
      return msgs;
    } catch {
      return null;
    }
  }, [fetchPage, pageSize]);

  return {
    persisted,
    setPersisted,
    persistedRef,
    hasMoreOlder,
    loadingOlder,
    setHasMoreOlder,
    onReachTop,
    bindScroll,
    fetchFirstPage,
    applyFirstPage,
    reloadMessages,
  };
}
