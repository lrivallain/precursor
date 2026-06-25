import { useEffect, useRef, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

interface Props {
  /** Title shown in the OS window chrome / browser tab. */
  title: string;
  defaultWidth?: number;
  defaultHeight?: number;
  /**
   * Called when the popup is closed by the user (e.g. they hit the OS close
   * button or popups are blocked) rather than programmatically on unmount. Lets
   * the host re-dock the content into the tab so an in-progress draft is never
   * lost.
   */
  onUserClose: () => void;
  children: ReactNode;
}

/** Mirror the host document's stylesheets so Tailwind / theme tokens apply. */
function copyStyles(source: Document, target: Document): void {
  source
    .querySelectorAll('style, link[rel="stylesheet"]')
    .forEach((node) => target.head.appendChild(node.cloneNode(true)));
}

/**
 * Renders its children into a separate native browser window opened via
 * ``window.open``. Used by {@link CommandPanel} to let the notes / GitHub draft
 * scratch pad live outside the current tab while the user keeps working in it.
 *
 * The children are mounted into a **dedicated React root** inside the popup
 * (not a cross-window ``createPortal``) so that synthetic events — typing in the
 * textarea, clicking the action buttons — are delivered correctly. The popup
 * root is re-rendered whenever ``children`` change, so edits driven by the
 * host's state stay reflected. The window is torn down when this component
 * unmounts, which happens once the user takes a terminal action and the host
 * stops rendering the panel.
 */
export function DetachedWindowPortal({
  title,
  defaultWidth = 600,
  defaultHeight = 520,
  onUserClose,
  children,
}: Props) {
  // Keep the latest close handler reachable from the one-shot open effect.
  const onUserCloseRef = useRef(onUserClose);
  onUserCloseRef.current = onUserClose;
  const rootRef = useRef<Root | null>(null);
  const childrenRef = useRef(children);
  childrenRef.current = children;

  useEffect(() => {
    const width = Math.min(defaultWidth, window.screen.availWidth);
    const height = Math.min(defaultHeight, window.screen.availHeight);
    const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
    const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
    const features = `popup=yes,width=${width},height=${height},left=${left},top=${top}`;
    const win = window.open("", "", features);
    if (!win) {
      // Popup blocked — fall back to the in-tab panel.
      onUserCloseRef.current();
      return;
    }

    win.document.title = title;
    copyStyles(document, win.document);
    win.document.documentElement.className = document.documentElement.className;
    win.document.body.className = document.body.className;
    win.document.body.style.margin = "0";

    const mount = win.document.createElement("div");
    mount.style.height = "100vh";
    win.document.body.appendChild(mount);

    const root = createRoot(mount);
    rootRef.current = root;
    root.render(childrenRef.current);

    // Keep dark-mode / theme classes in sync with the host while detached.
    const themeObserver = new MutationObserver(() => {
      win.document.documentElement.className = document.documentElement.className;
    });
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    let unmounting = false;
    const handleClose = () => {
      if (!unmounting) onUserCloseRef.current();
    };
    win.addEventListener("beforeunload", handleClose);
    // Catch closes that don't emit beforeunload (e.g. OS-level window close).
    const poll = window.setInterval(() => {
      if (win.closed) {
        window.clearInterval(poll);
        handleClose();
      }
    }, 400);

    return () => {
      unmounting = true;
      window.clearInterval(poll);
      themeObserver.disconnect();
      win.removeEventListener("beforeunload", handleClose);
      rootRef.current = null;
      // Defer teardown out of React's commit phase to avoid unmount-while-
      // rendering warnings, then close the popup.
      queueMicrotask(() => {
        try {
          root.unmount();
        } catch {
          /* window already gone */
        }
        try {
          win.close();
        } catch {
          /* already closed */
        }
      });
    };
    // Open exactly once; later prop changes are pushed via the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-render the popup whenever the host pushes new children (e.g. edits).
  useEffect(() => {
    rootRef.current?.render(children);
  }, [children]);

  return null;
}
