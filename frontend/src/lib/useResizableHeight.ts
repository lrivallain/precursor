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
  const [preferredHeight, setHeight] = useState<number>(() => {
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
  const pointerCleanup = useRef<(() => void) | null>(null);
  const height = clamp(preferredHeight, min, max);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(storageKey, String(preferredHeight));
  }, [storageKey, preferredHeight]);

  const cancelResize = useCallback(() => pointerCleanup.current?.(), []);
  useEffect(() => cancelResize, [cancelResize]);

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault();
    cancelResize();
    const target = e.currentTarget;
    const pointerId = e.pointerId;
    const originY = e.clientY;
    const originHeight = height;
    const previousSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    target.focus({ preventScroll: true });
    target.setPointerCapture(pointerId);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "row-resize";
    function move(event: PointerEvent): void {
      if (event.pointerId !== pointerId) return;
      const delta = event.clientY - originY;
      setHeight(clamp(originHeight + (side === "bottom" ? delta : -delta), min, max));
    }
    function finish(): void {
      pointerCleanup.current = null;
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", finish);
      target.removeEventListener("pointercancel", finish);
      target.removeEventListener("lostpointercapture", finish);
      window.removeEventListener("blur", finish);
      if (target.hasPointerCapture(pointerId)) target.releasePointerCapture(pointerId);
      document.body.style.userSelect = previousSelect;
      document.body.style.cursor = previousCursor;
    }
    pointerCleanup.current = finish;
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", finish);
    target.addEventListener("pointercancel", finish);
    target.addEventListener("lostpointercapture", finish);
    window.addEventListener("blur", finish);
  }, [cancelResize, height, min, max, side]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setHeight(e.key === "Home" ? min : max);
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const delta = (e.key === "ArrowDown" ? 16 : -16) * (side === "bottom" ? 1 : -1);
      setHeight((value) => clamp(clamp(value, min, max) + delta, min, max));
    }
  }, [min, max, side]);

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
      onUp();
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [side, min, max]);

  return { height, onMouseDown, onPointerDown, onKeyDown, cancelResize };
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}
