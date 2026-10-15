import { createContext, useContext, useLayoutEffect, useState, type RefObject } from "react";

/** Toolbar width (px) below which the composer's pickers collapse to glyphs. */
export const COMPOSER_COMPACT_BELOW = 480;

const ComposerCompactContext = createContext(false);

export const ComposerCompactProvider = ComposerCompactContext.Provider;

/** True while the surrounding composer toolbar is too narrow for text pills. */
export function useComposerCompact(): boolean {
  return useContext(ComposerCompactContext);
}

// Measured on the toolbar itself rather than the viewport: the same composer
// sits in a full-width chat, a narrow workspace side panel or a phone.
export function useIsCompactWidth(ref: RefObject<HTMLElement | null>): boolean {
  const [compact, setCompact] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = (): void => setCompact(el.clientWidth < COMPOSER_COMPACT_BELOW);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return compact;
}
