import { useCallback, useState } from "react";

export interface BusyAction {
  busy: boolean;
  error: string | null;
  setError: (message: string | null) => void;
  /**
   * Run `fn` with `busy` set, recording a thrown error's message. Resolves to
   * whether it succeeded, so callers can roll back optimistic state.
   */
  run: (fn: () => Promise<unknown>, opts?: { clearError?: boolean }) => Promise<boolean>;
}

/** One in-flight action at a time, with its error shown inline. */
export function useBusyAction(): BusyAction {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback<BusyAction["run"]>(async (fn, { clearError = true } = {}) => {
    setBusy(true);
    if (clearError) setError(null);
    try {
      await fn();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, setError, run };
}
