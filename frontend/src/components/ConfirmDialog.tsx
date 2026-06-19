import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

type ConfirmVariant = "default" | "warning" | "danger";

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmVariant;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

interface PendingConfirm {
  options: ConfirmOptions;
  resolve: (ok: boolean) => void;
}

function defaultTitle(variant: ConfirmVariant): string {
  if (variant === "danger") return "Confirm destructive action";
  if (variant === "warning") return "Please confirm";
  return "Confirm action";
}

function confirmClass(variant: ConfirmVariant): string {
  if (variant === "danger") return "bg-red-600 hover:bg-red-500";
  if (variant === "warning") return "bg-amber-500 hover:bg-amber-400 text-black";
  return "bg-accent hover:opacity-90";
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<PendingConfirm[]>([]);
  const active = queue[0] ?? null;
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmBtnRef = useRef<HTMLButtonElement>(null);
  const cancelBtnRef = useRef<HTMLButtonElement>(null);
  const activeVariant = active?.options.variant ?? "default";

  const settleActive = useCallback((ok: boolean): void => {
    setQueue((prev) => {
      if (prev.length === 0) return prev;
      const [current, ...rest] = prev;
      current.resolve(ok);
      return rest;
    });
  }, []);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((resolve) => {
      setQueue((prev) => [...prev, { options, resolve }]);
    });
  }, []);

  useEffect(() => {
    if (!active) return;

    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusTarget = activeVariant === "danger" ? cancelBtnRef.current : confirmBtnRef.current;
    window.requestAnimationFrame(() => focusTarget?.focus());

    function trapTab(e: KeyboardEvent): void {
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => !el.hasAttribute("disabled"));
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const current = document.activeElement;

      if (e.shiftKey && current === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && current === last) {
        e.preventDefault();
        first.focus();
      }
    }

    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        e.preventDefault();
        settleActive(false);
        return;
      }
      if (
        e.key === "Enter" &&
        !e.shiftKey &&
        !(e.target instanceof HTMLTextAreaElement) &&
        !(e.target instanceof HTMLButtonElement && e.target === cancelBtnRef.current)
      ) {
        e.preventDefault();
        settleActive(true);
        return;
      }
      trapTab(e);
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [active, activeVariant, settleActive]);

  const value = useMemo(() => confirm, [confirm]);

  const title = active?.options.title ?? defaultTitle(activeVariant);
  const message = active?.options.message ?? "";
  const confirmLabel = active?.options.confirmLabel ?? "Confirm";
  const cancelLabel = active?.options.cancelLabel ?? "Cancel";

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      {active && (
        <div
          className="fixed inset-0 z-[80] bg-black/50 flex items-center justify-center p-4"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) settleActive(false);
          }}
        >
          <div
            ref={dialogRef}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-dialog-title"
            aria-describedby="confirm-dialog-message"
            className="w-[min(520px,100%)] rounded-lg border border-border bg-bg shadow-xl"
          >
            <div className="border-b border-border px-4 py-3">
              <h2 id="confirm-dialog-title" className="text-sm font-semibold">
                {title}
              </h2>
            </div>
            <div className="px-4 py-3">
              <p id="confirm-dialog-message" className="text-sm text-text whitespace-pre-wrap">
                {message}
              </p>
            </div>
            <div className="border-t border-border px-4 py-3 flex justify-end gap-2">
              <button
                ref={cancelBtnRef}
                type="button"
                className="px-3 py-1.5 rounded border border-border text-sm hover:bg-surface"
                onClick={() => settleActive(false)}
              >
                {cancelLabel}
              </button>
              <button
                ref={confirmBtnRef}
                type="button"
                className={`px-3 py-1.5 rounded text-sm text-white ${confirmClass(activeVariant)}`}
                onClick={() => settleActive(true)}
              >
                {confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm must be used within a ConfirmProvider");
  return ctx;
}
