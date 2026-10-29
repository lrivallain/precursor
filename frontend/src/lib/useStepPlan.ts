import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiErrorMessage } from "./api";
import { eventBus } from "./events";
import type { StepPlan } from "./types";

const POLL_MS = 3000;

export interface StepPlanState {
  plan: StepPlan | null;
  loading: boolean;
  /** The plan couldn't be loaded (gone, or the server failed). */
  loadError: string | null;
  /** The last action's failure, shown as a dismissible banner. */
  actionError: string | null;
  dismissActionError: () => void;
  reload: () => Promise<void>;
  /**
   * Run a mutation, then re-read the plan. A failure becomes `actionError`
   * (the API's own message, e.g. a 409 reason) and resolves to `undefined`.
   */
  mutate: <T>(action: () => Promise<T>) => Promise<T | undefined>;
}

/**
 * One step plan, kept fresh: it re-reads after every action of this view and
 * whenever the server publishes `step-plan.changed` for it (another window, a
 * model action finishing). `onChanged` lets the section refresh its plan list.
 */
export function useStepPlan(planId: number, onChanged: () => void): StepPlanState {
  const [plan, setPlan] = useState<StepPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Only the latest read may land: an older, slower one would revert the view.
  const readSeq = useRef(0);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  const read = useCallback(async (): Promise<void> => {
    const seq = ++readSeq.current;
    try {
      const next = await api.stepPlans.get(planId);
      if (seq !== readSeq.current) return;
      setPlan(next);
      setLoadError(null);
    } catch (err) {
      if (seq !== readSeq.current) return;
      setLoadError(apiErrorMessage(err, "Could not load this step plan."));
    } finally {
      if (seq === readSeq.current) setLoading(false);
    }
  }, [planId]);

  useEffect(() => {
    setPlan(null);
    setLoading(true);
    setLoadError(null);
    setActionError(null);
    void read();
    return () => {
      readSeq.current++;
    };
  }, [read]);

  useEffect(() => {
    eventBus.start();
    return eventBus.subscribe((event) => {
      if (event.type === "step-plan.changed" && event.step_plan_id === planId) void read();
    });
  }, [planId, read]);

  // Server events can be missed (a dropped stream, a reloaded server), so a
  // running plan is also re-read on a timer.
  const running = plan?.auto_state === "running";
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void read(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [running, read]);

  const mutate = useCallback(
    async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
      setActionError(null);
      try {
        const result = await action();
        await read();
        onChangedRef.current();
        return result;
      } catch (err) {
        setActionError(apiErrorMessage(err));
        // A failed model call may still have changed the plan (it is kept).
        await read();
        onChangedRef.current();
        return undefined;
      }
    },
    [read],
  );

  return {
    plan,
    loading,
    loadError,
    actionError,
    dismissActionError: () => setActionError(null),
    reload: read,
    mutate,
  };
}
