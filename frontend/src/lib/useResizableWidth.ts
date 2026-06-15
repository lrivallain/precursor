import { useCallback, useEffect, useRef, useState } from "react";

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
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === "undefined") return defaultWidth;
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return defaultWidth;
    const n = Number.parseInt(raw, 10);
    if (Number.isNaN(n)) return defaultWidth;
    return clamp(n, min, max);
  });
  const dragging = useRef(false);
  const startX = useRef(0);
  const startW = useRef(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, String(width));
  }, [storageKey, width]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      startX.current = e.clientX;
      startW.current = width;
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";
    },
    [width],
  );

  useEffect(() => {
    function onMove(e: MouseEvent): void {
      if (!dragging.current) return;
      const delta = e.clientX - startX.current;
      const next = side === "right" ? startW.current + delta : startW.current - delta;
      setWidth(clamp(next, min, max));
    }
    function onUp(): void {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [side, min, max]);

  return { width, onMouseDown };
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
