import { useRef } from "react";
import { clamp, useMouseDrag, usePersistedState } from "./resizeCore";

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
  const [size, setSize] = usePersistedState<Size>(
    storageKey,
    { width: defaultWidth, height: defaultHeight },
    (raw) => parseSize(raw, defaultWidth, defaultHeight),
    JSON.stringify,
  );
  const startSize = useRef(size);
  const onResizeStart = useMouseDrag({
    cursor: "nwse-resize",
    stopPropagation: true,
    onStart: () => {
      startSize.current = size;
    },
    onMove: (dx, dy) => {
      const maxW = maxWidth ?? window.innerWidth - 32;
      const maxH = maxHeight ?? window.innerHeight - 32;
      setSize({
        width: clamp(startSize.current.width + dx, minWidth, maxW),
        height: clamp(startSize.current.height + dy, minHeight, maxH),
      });
    },
  });
  return { size, onResizeStart };
}

function parseSize(raw: string, w: number, h: number): Size {
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
