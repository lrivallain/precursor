import { createContext, useContext, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** Toolbar width (px) below which the composer's pickers collapse to glyphs. */
export const COMPOSER_COMPACT_BELOW = 480;

const ComposerCompactContext = createContext(false);

export const ComposerCompactProvider = ComposerCompactContext.Provider;

/** True while the surrounding composer toolbar is too narrow for text pills. */
export function useComposerCompact(): boolean {
  return useContext(ComposerCompactContext);
}

// Measured on the toolbar itself rather than the viewport: the same composer
// sits in a full-width chat, a narrow workspace side panel or a phone. It goes
// compact only when it has to: below a hard floor, or when the labelled pills
// no longer fit on one row (the toolbar wraps).
//
// A compact toolbar can't tell whether the full one would fit now, so it
// re-probes: it briefly renders the full layout (before paint) whenever the
// toolbar widens or its content changes, and falls straight back if that
// wraps. Without it a transient state (e.g. the "Select a model…" placeholder
// while the catalog loads) would leave the composer compact for good.

// Vertical padding of the toolbar (pt-1 + pb-2) plus slack for sub-pixel rounding.
const ROW_SLACK = 14;

export function useIsCompactWidth(ref: RefObject<HTMLElement | null>): boolean {
  const [compact, setCompact] = useState(false);
  const [probe, setProbe] = useState(0);
  const compactRef = useRef(compact);
  compactRef.current = compact;
  const settling = useRef(false);

  const tooNarrowOrWrapped = (): boolean => {
    const el = ref.current;
    if (!el) return false;
    if (el.clientWidth < COMPOSER_COMPACT_BELOW) return true;
    const row = (el.lastElementChild as HTMLElement | null)?.offsetHeight ?? 0;
    return row > 0 && el.clientHeight > row + ROW_SLACK;
  };

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let lastWidth = el.clientWidth;
    let frame = 0;
    const requestProbe = (): void => {
      settling.current = true;
      setProbe((n) => n + 1);
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        settling.current = false;
      });
    };
    const onResize = (): void => {
      const width = el.clientWidth;
      if (compactRef.current) {
        if (width > lastWidth && !settling.current) requestProbe();
      } else if (tooNarrowOrWrapped()) {
        setCompact(true);
      }
      lastWidth = width;
    };
    // Our own probe re-renders the toolbar; `settling` keeps those mutations
    // from requesting another probe.
    const mutations = new MutationObserver(() => {
      if (!settling.current && compactRef.current && el.clientWidth >= COMPOSER_COMPACT_BELOW) requestProbe();
    });
    onResize();
    const resize = new ResizeObserver(onResize);
    resize.observe(el);
    mutations.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      mutations.disconnect();
    };
  }, [ref]);

  useLayoutEffect(() => {
    if (probe > 0 && compactRef.current && ref.current && ref.current.clientWidth >= COMPOSER_COMPACT_BELOW) {
      setCompact(false);
    }
  }, [probe, ref]);

  useLayoutEffect(() => {
    if (!compact && tooNarrowOrWrapped()) setCompact(true);
  });

  return compact;
}
