import { useCallback, useState } from "react";

/**
 * A value shown briefly, e.g. which "Copy" button just succeeded. `flash(v)`
 * sets it and clears it after `ms`, unless another flash replaced it meanwhile.
 */
export function useFlash<T>(ms: number): [T | null, (value: T) => void] {
  const [value, setValue] = useState<T | null>(null);
  const flash = useCallback(
    (next: T) => {
      setValue(next);
      window.setTimeout(() => setValue((current) => (current === next ? null : current)), ms);
    },
    [ms],
  );
  return [value, flash];
}
