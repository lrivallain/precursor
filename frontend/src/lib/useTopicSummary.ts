import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { coalesce, eventBus } from "./events";
import type { TopicSummary } from "./types";

export interface TopicSummaryController {
  summary: TopicSummary | null;
  busy: boolean;
  error: string | null;
  refreshNotice: string | null;
  clearError: () => void;
  refresh: (instruction?: string) => Promise<boolean>;
  setVisible: (visible: boolean) => Promise<boolean>;
  toggleVisible: () => Promise<boolean>;
  addItem: (kind: "todo" | "important", text: string) => Promise<boolean>;
  save: (content: string, revision: string) => Promise<TopicSummary | null>;
  resolve: (accepted: number[], revision: string) => Promise<boolean>;
  remove: (revision: string) => Promise<boolean>;
}

export function useTopicSummary(topicId: number): TopicSummaryController {
  const [summary, setSummary] = useState<TopicSummary | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshNotice, setRefreshNotice] = useState<string | null>(null);
  const state = useMemo(() => ({
    active: false,
    sequence: 0,
    pending: 0,
    blocking: 0,
    invalidated: false,
    queue: Promise.resolve(),
    latest: null as TopicSummary | null,
    noticeTimer: null as ReturnType<typeof setTimeout> | null,
  }), [topicId]);

  const clearNotice = useCallback(() => {
    if (state.noticeTimer !== null) clearTimeout(state.noticeTimer);
    state.noticeTimer = null;
    if (state.active) setRefreshNotice(null);
  }, [state]);

  const load = useCallback(async (): Promise<void> => {
    if (state.pending) {
      state.invalidated = true;
      return;
    }
    const sequence = ++state.sequence;
    try {
      const loaded = await api.topicSummary.get(topicId);
      if (state.active && sequence === state.sequence) {
        if (loaded?.revision !== state.latest?.revision) clearNotice();
        state.latest = loaded;
        setSummary(loaded);
      }
    } catch (err) {
      if (state.active && sequence === state.sequence) setError((err as Error).message);
    } finally {
      if (state.active && sequence === state.sequence) setBusy(false);
    }
  }, [clearNotice, state, topicId]);

  useEffect(() => {
    state.active = true;
    state.latest = null;
    clearNotice();
    setSummary(null);
    setError(null);
    setBusy(true);
    void load();
    const reload = coalesce(load);
    const unsubscribe = eventBus.subscribe((event) => {
      if (event.type === "topic-summary.changed" && event.topic_id === topicId) reload();
    });
    return () => {
      state.active = false;
      clearNotice();
      ++state.sequence;
      reload.cancel();
      unsubscribe();
    };
  }, [clearNotice, load, state, topicId]);

  // All mutations share one queue. An initial fetch or cross-window reload
  // cannot overwrite a mutation response, and one completion cannot clear the
  // busy flag while another operation is still waiting.
  const run = useCallback(
    (action: () => Promise<TopicSummary | null>, background = false): Promise<boolean> => {
      ++state.pending;
      ++state.sequence;
      clearNotice();
      if (!background) ++state.blocking;
      if (state.active && !background) setBusy(true);
      const result = state.queue.then(async () => {
        // Autosaves must finish even when navigation has unmounted the panel.
        if (!state.active && !background) {
          --state.pending;
          --state.blocking;
          return false;
        }
        clearNotice();
        if (state.active) setError(null);
        try {
          const updated = await action();
          if (state.active) {
            state.latest = updated;
            setSummary(updated);
          }
          return true;
        } catch (err) {
          if (state.active) setError((err as Error).message);
          state.invalidated = true;
          return false;
        } finally {
          --state.pending;
          if (!background) --state.blocking;
          if (state.active) {
            setBusy(state.blocking > 0);
            if (!state.pending && state.invalidated) {
              state.invalidated = false;
              void load();
            }
          }
        }
      });
      state.queue = result.then(() => undefined);
      return result;
    },
    [clearNotice, load, state],
  );

  const setVisible = useCallback(
    (visible: boolean) => run(() => api.topicSummary.setVisible(topicId, visible)),
    [run, topicId],
  );
  const toggleVisible = useCallback(
    () => setVisible(!(summary?.visible ?? false)),
    [setVisible, summary?.visible],
  );

  return {
    summary,
    busy,
    error,
    refreshNotice,
    clearError: () => setError(null),
    refresh: (instruction) => run(async () => {
      // Compare with the state at execution time, after any queued autosave.
      const previous = state.latest;
      const updated = await api.topicSummary.generate(topicId, instruction);
      if (state.active && previous && updated.content === previous.content && !updated.suggestion) {
        setRefreshNotice("Summary refreshed — no changes");
        state.noticeTimer = setTimeout(() => {
          state.noticeTimer = null;
          if (state.active) setRefreshNotice(null);
        }, 3000);
      }
      return updated;
    }),
    setVisible,
    toggleVisible,
    addItem: (kind, text) => run(() => api.topicSummary.addItem(topicId, kind, text)),
    save: async (content, revision) => {
      let saved: TopicSummary | null = null;
      await run(async () => {
        saved = await api.topicSummary.save(topicId, content, revision);
        return saved;
      }, true);
      return saved;
    },
    resolve: (accepted, revision) => run(() => api.topicSummary.resolve(topicId, accepted, revision)),
    remove: (revision) => run(async () => {
      await api.topicSummary.remove(topicId, revision);
      return null;
    }),
  };
}
