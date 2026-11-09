import { useCallback, useEffect, useRef } from "react";
import { clamp, parseClampedInt, useMouseDrag, usePersistedState } from "./resizeCore";

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
  const [preferredHeight, setHeight] = usePersistedState(
    storageKey,
    defaultHeight,
    parseClampedInt(min, max),
  );
  const startHeight = useRef(0);
  const pointerCleanup = useRef<(() => void) | null>(null);
  const height = clamp(preferredHeight, min, max);

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
  }, [cancelResize, height, min, max, side, setHeight]);

  const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setHeight(e.key === "Home" ? min : max);
    } else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const delta = (e.key === "ArrowDown" ? 16 : -16) * (side === "bottom" ? 1 : -1);
      setHeight((value) => clamp(clamp(value, min, max) + delta, min, max));
    }
  }, [min, max, side, setHeight]);

  const onMouseDown = useMouseDrag({
    cursor: "row-resize",
    onStart: () => {
      startHeight.current = height;
    },
    onMove: (_dx, dy) => {
      const next = side === "top" ? startHeight.current - dy : startHeight.current + dy;
      setHeight(clamp(next, min, max));
    },
  });

  return { height, onMouseDown, onPointerDown, onKeyDown, cancelResize };
}

