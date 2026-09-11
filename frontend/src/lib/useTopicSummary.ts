import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { TopicSummary } from "./types";

export interface TopicSummaryController {
  summary: TopicSummary | null;
  busy: boolean;
  error: string | null;
  clearError: () => void;
  /** Replace the local copy after an edit / review (null = deleted). */
  apply: (summary: TopicSummary | null) => void;
  /** Regenerate from the conversation, notes and attachments. */
  refresh: (instruction?: string) => Promise<void>;
  setVisible: (visible: boolean) => Promise<void>;
  toggleVisible: () => Promise<void>;
  addItem: (kind: "todo" | "important", text: string) => Promise<void>;
}

/**
 * Loads and mutates a topic's editable summary.
 *
 * A topic has no summary until one is asked for, so `summary` stays null until
 * the user runs `/update-summary`, adds an item, or opens the panel from the
 * header — which is why every mutation returns the freshly-created row.
 */
export function useTopicSummary(topicId: number): TopicSummaryController {
  const [summary, setSummary] = useState<TopicSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSummary(null);
    setError(null);
    void (async () => {
      try {
        const loaded = await api.topicSummary.get(topicId);
        if (!cancelled) setSummary(loaded);
      } catch {
        // Unreachable API — behave as "no summary" rather than blocking chat.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [topicId]);

  const run = useCallback(
    async (action: () => Promise<TopicSummary>): Promise<void> => {
      setBusy(true);
      setError(null);
      try {
        setSummary(await action());
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const refresh = useCallback(
    (instruction?: string) =>
      run(() => api.topicSummary.generate(topicId, instruction)),
    [run, topicId],
  );

  const setVisible = useCallback(
    (visible: boolean) => run(() => api.topicSummary.setVisible(topicId, visible)),
    [run, topicId],
  );

  const toggleVisible = useCallback(
    () => setVisible(!(summary?.visible ?? false)),
    [setVisible, summary?.visible],
  );

  const addItem = useCallback(
    (kind: "todo" | "important", text: string) =>
      run(() => api.topicSummary.addItem(topicId, kind, text)),
    [run, topicId],
  );

  return {
    summary,
    busy,
    error,
    clearError: () => setError(null),
    apply: setSummary,
    refresh,
    setVisible,
    toggleVisible,
    addItem,
  };
}
