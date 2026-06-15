import { useCallback, useEffect, useRef, useState } from "react";

interface Options {
  storageKey: string;
  defaultHeight: number;
  min: number;
  max: number;
  /** "top" handle increases height as cursor moves up (default — handle sits
   *  on top of a bottom-anchored panel). "bottom" handle grows downward. */
  side?: "top" | "bottom";
}

export function useResizableHeight({
  storageKey,
  defaultHeight,
  min,
  max,
  side = "top",
}: Options) {
  const [height, setHeight] = useState<number>(() => {
    if (typeof window === "undefined") return defaultHeight;
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return defaultHeight;
    const n = Number.parseInt(raw, 10);
    if (Number.isNaN(n)) return defaultHeight;
    return clamp(n, min, max);
  });
  const dragging = useRef(false);
  const startY = useRef(0);
  const startH = useRef(0);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, String(height));
  }, [storageKey, height]);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      startY.current = e.clientY;
      startH.current = height;
      document.body.style.userSelect = "none";
      document.body.style.cursor = "row-resize";
    },
    [height],
  );

  useEffect(() => {
    function onMove(e: MouseEvent): void {
      if (!dragging.current) return;
      const delta = e.clientY - startY.current;
      const next = side === "top" ? startH.current - delta : startH.current + delta;
      setHeight(clamp(next, min, max));
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

  return { height, onMouseDown };
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
