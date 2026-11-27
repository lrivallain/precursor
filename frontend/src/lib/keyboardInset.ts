// Keep the app above the on-screen keyboard.
//
// Chrome and Edge on Android shrink the page for the keyboard thanks to
// `interactive-widget=resizes-content` (index.html). iOS Safari ignores that:
// it only shrinks the visual viewport, so the 100dvh shell keeps its height and
// the composer ends up under the keyboard. There, the shell follows the visual
// viewport's height while the keyboard is up (`--app-height`, index.css).

/** The keyboard's height, or 0 when it isn't up (or the page is pinch-zoomed). */
export function keyboardHeight(visualHeight: number, layoutHeight: number, scale: number): number {
  if (scale > 1.01) return 0; // a zoomed page also has a smaller visual viewport
  const gap = layoutHeight - visualHeight;
  return gap > 80 ? gap : 0; // below that it's browser chrome, not a keyboard
}

export function installKeyboardInset(): void {
  const viewport = window.visualViewport;
  if (!viewport) return;
  const root = document.documentElement;
  const update = () => {
    const keyboard = keyboardHeight(viewport.height, window.innerHeight, viewport.scale);
    if (keyboard > 0) {
      root.style.setProperty("--app-height", `${viewport.height}px`);
      // Safari scrolls the page to reveal the field; the shell now fits, so put it back.
      if (window.scrollY !== 0) window.scrollTo(0, 0);
    } else {
      root.style.removeProperty("--app-height");
    }
  };
  viewport.addEventListener("resize", update);
  viewport.addEventListener("scroll", update);
  update();
}
