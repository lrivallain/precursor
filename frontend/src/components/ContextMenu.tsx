import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { LucideIcon } from "lucide-react";
import { Z_INDEX } from "../lib/constants";

export interface ContextMenuItem {
  label: string;
  icon: LucideIcon;
  onSelect: () => void | Promise<void>;
  danger?: boolean;
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
      {items.map(({ label: itemLabel, icon: Icon, onSelect, danger }) => (
        <button
          key={itemLabel}
          type="button"
          role="menuitem"
          className={`flex w-full items-center gap-2 px-3 py-1.5 text-left outline-none hover:bg-surface focus:bg-surface ${
            danger ? "text-red-500" : ""
          }`}
          onClick={() => {
            onClose();
            void onSelect();
          }}
        >
          <Icon size={14} className={danger ? "" : "text-muted"} />
          <span>{itemLabel}</span>
        </button>
      ))}
    </div>,
    document.body,
  );
}
