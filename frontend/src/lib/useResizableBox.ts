import { useCallback, useEffect, useRef, useState } from "react";

interface Size {
  width: number;
  height: number;
}

interface Options {
  storageKey: string;
  defaultWidth: number;
  defaultHeight: number;
  minWidth: number;
  minHeight: number;
  /** Upper bounds default to the viewport (recomputed on each drag). */
  maxWidth?: number;
  maxHeight?: number;
}

/**
 * Two-axis resizable box with a bottom-right corner grip, persisted to
 * localStorage. Mirrors useResizableWidth's conventions but drives explicit
 * width + height on a centered panel (e.g. a modal). Bounds clamp to the
 * viewport so a persisted size never exceeds the current window.
 */
export function useResizableBox({
  storageKey,
  defaultWidth,
  defaultHeight,
  minWidth,
  minHeight,
  maxWidth,
  maxHeight,
}: Options) {
  const [size, setSize] = useState<Size>(() => readStored(storageKey, defaultWidth, defaultHeight));
  const dragging = useRef(false);
  const start = useRef({ x: 0, y: 0, w: 0, h: 0 });

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify(size));
  }, [storageKey, size]);

  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      dragging.current = true;
      start.current = { x: e.clientX, y: e.clientY, w: size.width, h: size.height };
      document.body.style.userSelect = "none";
      document.body.style.cursor = "nwse-resize";
    },
    [size.width, size.height],
  );

  useEffect(() => {
    function onMove(e: MouseEvent): void {
      if (!dragging.current) return;
      const maxW = maxWidth ?? window.innerWidth - 32;
      const maxH = maxHeight ?? window.innerHeight - 32;
      const width = clamp(start.current.w + (e.clientX - start.current.x), minWidth, maxW);
      const height = clamp(start.current.h + (e.clientY - start.current.y), minHeight, maxH);
      setSize({ width, height });
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
  }, [minWidth, minHeight, maxWidth, maxHeight]);

  return { size, onResizeStart };
}

function readStored(key: string, w: number, h: number): Size {
  if (typeof window === "undefined") return { width: w, height: h };
  const raw = window.localStorage.getItem(key);
  if (!raw) return { width: w, height: h };
  try {
    const parsed = JSON.parse(raw) as Partial<Size>;
    return {
      width: typeof parsed.width === "number" ? parsed.width : w,
      height: typeof parsed.height === "number" ? parsed.height : h,
    };
  } catch {
    return { width: w, height: h };
  }
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(Math.max(min, max), n));
}
