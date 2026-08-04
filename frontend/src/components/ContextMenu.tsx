import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Z_INDEX } from "../lib/constants";

export interface ContextMenuItem {
  label: string;
  icon: LucideIcon;
  onSelect?: () => void | Promise<void>;
  danger?: boolean;
  /** Nested choices. When present the row opens a flyout instead of acting. */
  submenu?: ContextMenuSubItem[];
}

export interface ContextMenuSubItem {
  label: string;
  onSelect: () => void | Promise<void>;
  /** Full Tailwind class for a small leading dot (e.g. a collection accent). */
  dot?: string;
  checked?: boolean;
}

interface Props {
  x: number;
  y: number;
  label: string;
  items: ContextMenuItem[];
  onClose: () => void;
}

export function ContextMenu({ x, y, label, items, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: x, top: y });
  const [openSub, setOpenSub] = useState<string | null>(null);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    setPosition({
      left: Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)),
      top: Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)),
    });
  }, [x, y]);

  useEffect(() => {
    const close = () => onClose();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", onKeyDown);
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => {
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      aria-label={label}
      className="fixed min-w-48 rounded-md border border-border bg-bg py-1 text-sm shadow-lg"
      style={{ left: position.left, top: position.top, zIndex: Z_INDEX.MODAL }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {items.map(({ label: itemLabel, icon: Icon, onSelect, danger, submenu }) =>
        submenu ? (
          <div
            key={itemLabel}
            className="relative"
            onPointerEnter={() => setOpenSub(itemLabel)}
            onPointerLeave={() => setOpenSub((cur) => (cur === itemLabel ? null : cur))}
          >
            <button
              type="button"
              role="menuitem"
              aria-haspopup="menu"
              aria-expanded={openSub === itemLabel}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left outline-none hover:bg-surface focus:bg-surface"
              onClick={() => setOpenSub((cur) => (cur === itemLabel ? null : itemLabel))}
            >
              <Icon size={14} className="text-muted" />
              <span className="flex-1">{itemLabel}</span>
              <ChevronRight size={13} className="text-muted" />
            </button>
            {openSub === itemLabel && submenu.length > 0 && (
              <div
                role="menu"
                aria-label={itemLabel}
                className="absolute left-full top-0 -mt-1 ml-0.5 min-w-40 max-h-72 overflow-y-auto rounded-md border border-border bg-bg py-1 shadow-lg"
              >
                {submenu.map((sub) => (
                  <button
                    key={sub.label}
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left outline-none hover:bg-surface focus:bg-surface"
                    onClick={() => {
                      onClose();
                      void sub.onSelect();
                    }}
                  >
                    {sub.dot && (
                      <span className={`h-2 w-2 shrink-0 rounded-full ${sub.dot}`} aria-hidden />
                    )}
                    <span className="flex-1 truncate">{sub.label}</span>
                    {sub.checked && <Check size={13} className="shrink-0 text-muted" />}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <button
            key={itemLabel}
            type="button"
            role="menuitem"
            className={`flex w-full items-center gap-2 px-3 py-1.5 text-left outline-none hover:bg-surface focus:bg-surface ${
              danger ? "text-red-500" : ""
            }`}
            onClick={() => {
              onClose();
              void onSelect?.();
            }}
          >
            <Icon size={14} className={danger ? "" : "text-muted"} />
            <span>{itemLabel}</span>
          </button>
        ),
      )}
    </div>,
    document.body,
  );
}
