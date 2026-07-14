import { useEffect } from "react";
import type { ReactNode } from "react";
import { Z_INDEX } from "../lib/constants";

export interface ModalProps {
  /** Dismiss handler, invoked on backdrop click and Escape (when enabled). */
  onClose?: () => void;
  children: ReactNode;
  /** Class list for the centered panel (width, background, border, padding). */
  panelClassName?: string;
  /** Backdrop tint utility. Defaults to a translucent black scrim. */
  backdropClassName?: string;
  /** Stacking tier from the shared Z_INDEX scale. Defaults to MODAL. */
  zIndex?: string;
  /** Add padding around the panel so it never touches the viewport edge. */
  padded?: boolean;
  /** Close when the backdrop (outside the panel) is clicked. Default true. */
  closeOnBackdrop?: boolean;
  /** Close when Escape is pressed. Default false to match legacy modals. */
  closeOnEscape?: boolean;
  role?: "dialog" | "alertdialog";
  labelledBy?: string;
  describedBy?: string;
}

/**
 * Backdrop + centered panel shell shared by the app's modals. Owns the scrim,
 * outside-click dismissal, and optional Escape handling so individual dialogs
 * only supply their panel content. The click test uses mousedown target
 * identity so dragging a selection out of the panel doesn't dismiss it.
 */
export function Modal({
  onClose,
  children,
  panelClassName,
  backdropClassName = "bg-black/40",
  zIndex = Z_INDEX.MODAL,
  padded = false,
  closeOnBackdrop = true,
  closeOnEscape = false,
  role = "dialog",
  labelledBy,
  describedBy,
}: ModalProps) {
  useEffect(() => {
    if (!closeOnEscape) return;
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose?.();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeOnEscape, onClose]);

  return (
    <div
      className={`fixed inset-0 flex items-center justify-center ${backdropClassName} ${zIndex}${
        padded ? " p-4" : ""
      }`}
      onMouseDown={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        role={role}
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        className={panelClassName}
      >
        {children}
      </div>
    </div>
  );
}
