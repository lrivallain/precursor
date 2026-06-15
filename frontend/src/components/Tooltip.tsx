import { useEffect, useLayoutEffect, useRef, useState } from "react";

const SHOW_DELAY_MS = 200;
const EDGE_PAD = 6;
const ANCHOR_GAP = 6;

interface AnchorRect {
  text: string;
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
}

interface FinalPos {
  left: number;
  top: number;
}

/**
 * Hover-tooltip provider. Mount once at the app root. Any element with a
 * `data-tooltip="..."` attribute gets a snappy custom tooltip on hover
 * (200ms show, instant hide). The tooltip auto-clamps to the viewport on
 * all four edges by measuring its rendered size after mount.
 */
export function TooltipProvider() {
  const [anchor, setAnchor] = useState<AnchorRect | null>(null);
  const [pos, setPos] = useState<FinalPos | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const showTimer = useRef<number | null>(null);
  const currentTarget = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const clearShow = () => {
      if (showTimer.current !== null) {
        window.clearTimeout(showTimer.current);
        showTimer.current = null;
      }
    };
    const hide = () => {
      clearShow();
      currentTarget.current = null;
      setAnchor(null);
      setPos(null);
    };

    const onOver = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      const host = target.closest<HTMLElement>("[data-tooltip]");
      if (!host) {
        if (currentTarget.current) hide();
        return;
      }
      const text = host.dataset.tooltip ?? "";
      if (!text) return;
      if (host === currentTarget.current) return;
      clearShow();
      currentTarget.current = host;
      showTimer.current = window.setTimeout(() => {
        if (!host.isConnected) return;
        const r = host.getBoundingClientRect();
        setPos(null);
        setAnchor({
          text,
          left: r.left,
          top: r.top,
          right: r.right,
          bottom: r.bottom,
          width: r.width,
          height: r.height,
        });
      }, SHOW_DELAY_MS);
    };

    const onOut = (e: MouseEvent) => {
      const related = e.relatedTarget as HTMLElement | null;
      if (
        currentTarget.current &&
        (!related || !currentTarget.current.contains(related))
      ) {
        hide();
      }
    };

    document.addEventListener("mouseover", onOver);
    document.addEventListener("mouseout", onOut);
    document.addEventListener("scroll", hide, true);
    document.addEventListener("mousedown", hide, true);
    document.addEventListener("keydown", hide, true);
    window.addEventListener("blur", hide);

    return () => {
      clearShow();
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mouseout", onOut);
      document.removeEventListener("scroll", hide, true);
      document.removeEventListener("mousedown", hide, true);
      document.removeEventListener("keydown", hide, true);
      window.removeEventListener("blur", hide);
    };
  }, []);

  // After the tooltip mounts, measure it and compute a clamped position.
  // First render places it invisibly; the layout effect repositions before
  // paint so users only ever see the final clamped placement.
  useLayoutEffect(() => {
    if (!anchor || !tipRef.current) return;
    const tw = tipRef.current.offsetWidth;
    const th = tipRef.current.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Vertical: prefer above; flip below when there isn't enough room.
    const spaceAbove = anchor.top;
    const spaceBelow = vh - anchor.bottom;
    const placeBelow =
      spaceAbove < th + ANCHOR_GAP + EDGE_PAD && spaceBelow > spaceAbove;
    let top = placeBelow
      ? anchor.bottom + ANCHOR_GAP
      : anchor.top - ANCHOR_GAP - th;
    top = Math.max(EDGE_PAD, Math.min(vh - th - EDGE_PAD, top));

    // Horizontal: center on the anchor, then clamp into the viewport.
    const cx = anchor.left + anchor.width / 2;
    let left = cx - tw / 2;
    left = Math.max(EDGE_PAD, Math.min(vw - tw - EDGE_PAD, left));

    setPos({ left, top });
  }, [anchor]);

  if (!anchor) return null;

  return (
    <div
      ref={tipRef}
      role="tooltip"
      aria-hidden="true"
      style={{
        position: "fixed",
        left: pos?.left ?? 0,
        top: pos?.top ?? 0,
        zIndex: 100,
        pointerEvents: "none",
        maxWidth: "min(20rem, calc(100vw - 12px))",
        visibility: pos ? "visible" : "hidden",
      }}
      className="px-2 py-1 rounded text-[11px] leading-tight bg-text text-bg shadow-lg whitespace-pre"
    >
      {anchor.text}
    </div>
  );
}
