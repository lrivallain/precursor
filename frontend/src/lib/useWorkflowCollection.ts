import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Workflow } from "./types";

/** One collection backs both the persistent list and the overview/detail pane. */
export function useWorkflowCollection(enabled: boolean, reloadKey: number) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef(false);
  const revision = useRef(0);
  const inFlight = useRef(false);
  const queued = useRef(false);
  const reloadLatest = useRef<() => void>(() => {});

  const reload = useCallback(async () => {
    if (!enabled) return;
    // Slow responses must complete even when polling/SSE asks for another
    // refresh. Queue one follow-up rather than continually superseding them.
    if (inFlight.current) {
      queued.current = true;
      return;
    }
    inFlight.current = true;
    const request = ++revision.current;
    if (!loaded.current) setLoading(true);
    try {
      const items = await api.workflows.list();
      if (request !== revision.current) return;
      setWorkflows(items);
      loaded.current = true;
      setError(null);
    } catch (err) {
      if (request !== revision.current) return;
      setError(err instanceof Error ? err.message : "Could not load workflows.");
    } finally {
      if (request === revision.current) setLoading(false);
      inFlight.current = false;
      if (queued.current) {
        queued.current = false;
        reloadLatest.current();
      }
    }
  }, [enabled]);

  useEffect(() => {
    reloadLatest.current = () => { void reload(); };
    void reload();
    return () => {
      revision.current += 1;
      reloadLatest.current = () => {};
    };
  }, [reload, reloadKey]);

  const running = workflows.some((workflow) => workflow.status === "running");
  useEffect(() => {
    if (!enabled || !running) return;
    const timer = window.setInterval(() => void reload(), 2000);
    return () => window.clearInterval(timer);
  }, [enabled, running, reload]);

  const upsert = useCallback((workflow: Workflow) => {
    // A response started before an edit/run must not overwrite that change.
    revision.current += 1;
    setLoading(false);
    setWorkflows((prev) => prev.some((item) => item.id === workflow.id)
      ? prev.map((item) => item.id === workflow.id ? workflow : item)
      : [workflow, ...prev]);
  }, []);

  const remove = useCallback((id: number) => {
    revision.current += 1;
    setLoading(false);
    setWorkflows((prev) => prev.filter((item) => item.id !== id));
  }, []);

  return { workflows, loading, error, reload, upsert, remove };
}
