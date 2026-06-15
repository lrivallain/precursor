import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { useSettings } from "./settingsStore";
import type { IssueSummary, Topic } from "./types";

export type IssueContextStatus =
  | "no-issue"
  | "no-repo"
  | "idle"
  | "loading"
  | "ready"
  | "error";

export interface IssueContextState {
  status: IssueContextStatus;
  summary: IssueSummary | null;
  error: string | null;
  effectiveRepo: string;
  hasIssue: boolean;
  creating: boolean;
  pushing: boolean;
  refresh: (opts?: { force?: boolean }) => Promise<void>;
  createAndLink: () => Promise<Topic | null>;
  pushToIssue: () => Promise<void>;
}

/**
 * Owns the GitHub-issue context for a given topic: tracks load status,
 * exposes sync/create/update actions, and auto-refreshes when the topic
 * changes. Designed to be instantiated once per active topic at the app
 * level and shared between the title-bar indicator and the settings tab.
 */
export function useIssueContext(
  topic: Topic | null,
  onTopicChanged: (topic: Topic) => void,
): IssueContextState {
  const [summary, setSummary] = useState<IssueSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<IssueContextStatus>("idle");
  const [creating, setCreating] = useState(false);
  const [pushing, setPushing] = useState(false);
  const [globalRepo, setGlobalRepo] = useState("");
  const settings = useSettings();
  const enabled = settings?.issue_associations_enabled ?? true;

  useEffect(() => {
    void (async () => {
      try {
        const s = await api.getSettings();
        setGlobalRepo(s.github_repo);
      } catch {
        /* optional */
      }
    })();
  }, []);

  const hasIssue = !!topic && topic.github_issue_number !== null;
  const effectiveRepo = topic ? topic.github_repo || globalRepo : "";

  const refresh = useCallback(
    async (opts: { force?: boolean } = {}): Promise<void> => {
      if (!enabled || !topic || !hasIssue || !effectiveRepo) return;
      setStatus("loading");
      setError(null);
      try {
        setSummary(await api.summarizeIssue(topic.id, opts));
        setStatus("ready");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setStatus("error");
      }
    },
    [topic, hasIssue, effectiveRepo, enabled],
  );

  // Auto-refresh on topic change. Reset visible state first to avoid flashing
  // the previous topic's data.
  useEffect(() => {
    setSummary(null);
    setError(null);
    if (!enabled) {
      setStatus("idle");
      return;
    }
    if (!topic) {
      setStatus("idle");
      return;
    }
    if (!hasIssue) {
      setStatus("no-issue");
      return;
    }
    if (!effectiveRepo) {
      setStatus("no-repo");
      return;
    }
    void refresh();
  }, [topic?.id, hasIssue, effectiveRepo, refresh, enabled]);

  const createAndLink = useCallback(async (): Promise<Topic | null> => {
    if (!topic || creating) return null;
    setCreating(true);
    setError(null);
    try {
      const repo = topic.github_repo || undefined;
      const issue = await api.createIssue({
        repo,
        title: topic.title,
        body: topic.description ?? `Tracking topic created from Precursor: ${topic.title}`,
      });
      const updated = await api.updateTopic(topic.id, {
        github_issue_number: issue.number,
        github_repo: repo ?? null,
      });
      onTopicChanged(updated);
      return updated;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
      return null;
    } finally {
      setCreating(false);
    }
  }, [topic, creating, onTopicChanged]);

  const pushToIssue = useCallback(async (): Promise<void> => {
    if (!topic || pushing) return;
    setPushing(true);
    setError(null);
    try {
      await api.pushIssue(topic.id);
      // Body on GitHub changed — force a refresh so the summary is regenerated.
      await refresh({ force: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    } finally {
      setPushing(false);
    }
  }, [topic, pushing, refresh]);

  return {
    status,
    summary,
    error,
    effectiveRepo,
    hasIssue,
    creating,
    pushing,
    refresh,
    createAndLink,
    pushToIssue,
  };
}
