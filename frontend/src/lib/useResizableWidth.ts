import { useRef } from "react";
import { clamp, parseClampedInt, useMouseDrag, usePersistedState } from "./resizeCore";

interface Options {
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** "right" handle increases width as cursor moves right (default).
   *  "left" handle increases width as cursor moves left. */
  side?: "left" | "right";
}

export function useResizableWidth({
  storageKey,
  defaultWidth,
  min,
  max,
  side = "right",
}: Options) {
  const [width, setWidth] = usePersistedState(storageKey, defaultWidth, parseClampedInt(min, max));
  const startWidth = useRef(0);
  const onMouseDown = useMouseDrag({
    cursor: "col-resize",
    onStart: () => {
      startWidth.current = width;
    },
    onMove: (dx) => {
      const next = side === "right" ? startWidth.current + dx : startWidth.current - dx;
      setWidth(clamp(next, min, max));
    },
  });
  return { width, onMouseDown };
}
