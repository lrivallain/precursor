import { useCallback, useEffect, useRef, useState } from "react";
import { beginDrag } from "./dragGuard";

/**
 * Shared plumbing for the resizable panels (`useResizableWidth`,
 * `useResizableHeight`, `useResizableBox`): a size persisted to localStorage and
 * a mouse-drag session. Build new resize hooks on these rather than copying the
 * listeners and storage code again.
 */

export function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(Math.max(min, max), n));
}

/**
 * State initialised from `localStorage[key]` (via `parse`, falling back to
 * `fallback` when absent or invalid) and written back on every change.
 */
export function usePersistedState<T>(
  key: string,
  fallback: T,
  parse: (raw: string) => T | null,
  serialize: (value: T) => string = String,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return fallback;
    const raw = window.localStorage.getItem(key);
    return raw ? (parse(raw) ?? fallback) : fallback;
  });
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(key, serialize(value));
  }, [key, value, serialize]);
  return [value, setValue];
}

/** Parse a persisted integer and clamp it, or `null` when it isn't one. */
export function parseClampedInt(min: number, max: number): (raw: string) => number | null {
  return (raw) => {
    const n = Number.parseInt(raw, 10);
    return Number.isNaN(n) ? null : clamp(n, min, max);
  };
}

interface MouseDragOptions {
  /** Body cursor while dragging (`"col-resize"`, `"row-resize"`, ...). */
  cursor: string;
  /** Called on mouse down, e.g. to remember the size the drag starts from. */
  onStart: () => void;
  /** Called on every move with the offset from the mouse-down point. */
  onMove: (dx: number, dy: number) => void;
  /** Stop the mouse-down from reaching parent handlers (e.g. a draggable modal). */
  stopPropagation?: boolean;
}

/**
 * A mouse-drag session: returns the `onMouseDown` handler for the grip. While
 * dragging, text selection is disabled, the cursor is forced, and other drag
 * handlers are suspended (`beginDrag`). Ends on mouse up or window blur.
 */
export function useMouseDrag({
  cursor,
  onStart,
  onMove,
  stopPropagation = false,
}: MouseDragOptions): (e: React.MouseEvent) => void {
  const dragging = useRef(false);
  const origin = useRef({ x: 0, y: 0 });
  const endGuard = useRef<(() => void) | null>(null);
  const handlers = useRef({ onStart, onMove });
  handlers.current = { onStart, onMove };

  useEffect(() => {
    function move(e: MouseEvent): void {
      if (!dragging.current) return;
      handlers.current.onMove(e.clientX - origin.current.x, e.clientY - origin.current.y);
    }
    function end(): void {
      endGuard.current?.();
      endGuard.current = null;
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    }
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", end);
    window.addEventListener("blur", end);
    return () => {
      end();
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", end);
      window.removeEventListener("blur", end);
    };
  }, []);

  return useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      if (stopPropagation) e.stopPropagation();
      dragging.current = true;
      origin.current = { x: e.clientX, y: e.clientY };
      handlers.current.onStart();
      endGuard.current?.();
      endGuard.current = beginDrag();
      document.body.style.userSelect = "none";
      document.body.style.cursor = cursor;
    },
    [cursor, stopPropagation],
  );
}
