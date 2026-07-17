import { useCallback, useRef, useState } from "react";

import { api, apiErrorMessage } from "./api";

export interface UseRefineOptions {
  /** Context hint sent to the backend (e.g. "system_prompt", "note"). */
  kind?: string;
  /** Optional freeform steer appended to the rewrite prompt. */
  instruction?: string;
}

export interface UseRefine {
  /** Rewrite `text`; returns the refined text, or null on failure. */
  refine: (text: string) => Promise<string | null>;
  /** Return the pre-refine text (once), or null if nothing to revert to. */
  revert: () => string | null;
  /** Forget any captured original (call when the user edits the field). */
  reset: () => void;
  /** True while a suggestion is applied and the original is still recoverable. */
  canRevert: boolean;
  busy: boolean;
  error: string | null;
}

/**
 * Drives a "Refine with AI" control. The original text is held in a ref (memory
 * only, never persisted) so the caller can offer a one-tap revert after a
 * suggestion is applied.
 */
export function useRefine(opts: UseRefineOptions = {}): UseRefine {
  const { kind, instruction } = opts;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canRevert, setCanRevert] = useState(false);
  const previousRef = useRef<string | null>(null);

  const refine = useCallback(
    async (text: string): Promise<string | null> => {
      if (busy || !text.trim()) return null;
      setBusy(true);
      setError(null);
      try {
        const res = await api.ai.refine({ text, kind, instruction });
        previousRef.current = text;
        setCanRevert(true);
        return res.text;
      } catch (e) {
        setError(apiErrorMessage(e, "Refine failed"));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [busy, kind, instruction],
  );

  const revert = useCallback((): string | null => {
    const prev = previousRef.current;
    previousRef.current = null;
    setCanRevert(false);
    return prev;
  }, []);

  const reset = useCallback(() => {
    if (previousRef.current === null && !canRevert) return;
    previousRef.current = null;
    setCanRevert(false);
  }, [canRevert]);

  return { refine, revert, reset, canRevert, busy, error };
}
