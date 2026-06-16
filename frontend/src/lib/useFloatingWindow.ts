import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent } from "react";

interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface Options {
  storageKey: string;
  defaultWidth: number;
  defaultHeight: number;
  minWidth?: number;
  minHeight?: number;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function viewport(): { w: number; h: number } {
  if (typeof window === "undefined") return { w: 1280, h: 800 };
  return { w: window.innerWidth, h: window.innerHeight };
}

function clampRect(r: Rect, minW: number, minH: number): Rect {
  const { w, h } = viewport();
  const width = clamp(r.width, minW, Math.max(minW, w - 16));
  const height = clamp(r.height, minH, Math.max(minH, h - 16));
  const x = clamp(r.x, 0, Math.max(0, w - width));
  const y = clamp(r.y, 0, Math.max(0, h - height));
  return { x, y, width, height };
}

/**
 * Drives a draggable + resizable floating window whose position and size are
 * remembered across sessions (localStorage per ``storageKey``). Used by the
 * build-in command panels so the scratch pad / draft cards float over the chat
 * instead of sharing the bottom block with the message composer.
 *
 * Returns a ``style`` for the fixed-positioned container plus mouse-down
 * handlers to wire to the drag handle (header) and the resize grip (corner).
 */
export function useFloatingWindow({
  storageKey,
  defaultWidth,
  defaultHeight,
  minWidth = 360,
  minHeight = 240,
}: Options) {
  const [rect, setRect] = useState<Rect>(() => {
    const { w, h } = viewport();
    const width = Math.min(defaultWidth, w - 24);
    const height = Math.min(defaultHeight, h - 24);
    let base: Rect = {
      width,
      height,
      x: Math.round((w - width) / 2),
      y: Math.round(Math.min(96, h - height - 24)),
    };
    if (typeof window !== "undefined") {
      const raw = window.localStorage.getItem(storageKey);
      if (raw) {
        try {
          base = { ...base, ...(JSON.parse(raw) as Partial<Rect>) };
        } catch {
          /* ignore malformed persisted rect */
        }
      }
    }
    return clampRect(base, minWidth, minHeight);
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, JSON.stringify(rect));
  }, [storageKey, rect]);

  const drag = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(
    null,
  );
  const resize = useRef<{ startX: number; startY: number; origW: number; origH: number } | null>(
    null,
  );

  const onDragStart = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      drag.current = { startX: e.clientX, startY: e.clientY, origX: rect.x, origY: rect.y };
      document.body.style.userSelect = "none";
    },
    [rect.x, rect.y],
  );

  const onResizeStart = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      resize.current = {
        startX: e.clientX,
        startY: e.clientY,
        origW: rect.width,
        origH: rect.height,
      };
      document.body.style.userSelect = "none";
    },
    [rect.width, rect.height],
  );

  useEffect(() => {
    function onMove(e: MouseEvent): void {
      if (drag.current) {
        const { w, h } = viewport();
        const nx = drag.current.origX + (e.clientX - drag.current.startX);
        const ny = drag.current.origY + (e.clientY - drag.current.startY);
        setRect((r) => ({
          ...r,
          x: clamp(nx, 0, Math.max(0, w - r.width)),
          y: clamp(ny, 0, Math.max(0, h - r.height)),
        }));
      } else if (resize.current) {
        const { w, h } = viewport();
        setRect((r) => ({
          ...r,
          width: clamp(resize.current!.origW + (e.clientX - resize.current!.startX), minWidth, w - r.x - 8),
          height: clamp(resize.current!.origH + (e.clientY - resize.current!.startY), minHeight, h - r.y - 8),
        }));
      }
    }
    function onUp(): void {
      if (!drag.current && !resize.current) return;
      drag.current = null;
      resize.current = null;
      document.body.style.userSelect = "";
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [minWidth, minHeight]);

  useEffect(() => {
    function onWinResize(): void {
      setRect((r) => clampRect(r, minWidth, minHeight));
    }
    window.addEventListener("resize", onWinResize);
    return () => window.removeEventListener("resize", onWinResize);
  }, [minWidth, minHeight]);

  const style: CSSProperties = {
    position: "fixed",
    left: rect.x,
    top: rect.y,
    width: rect.width,
    height: rect.height,
  };

  return { style, onDragStart, onResizeStart };
}
