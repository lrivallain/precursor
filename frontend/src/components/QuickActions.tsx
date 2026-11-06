import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { Bot, FolderGit2, ListChecks, MessageSquare, MessagesSquare, Plus, Radio, Workflow } from "lucide-react";
import type { CoreSidebarMode } from "./Sidebar";
import { Z_INDEX } from "../lib/constants";
import { beginDrag } from "../lib/dragGuard";
import { sectionColor } from "../lib/sections";

type CreateSection = Exclude<CoreSidebarMode, "iq">;
type Position = { right: number; bottom: number };

const STORAGE_KEY = "precursor.quickActions.position";
const SIZE = 52;
const MARGIN = 16;
const GAP = 12;
const ARC_DEPTH = 24;
const DEFAULT_POSITION: Position = { right: 24, bottom: 24 };
const ACTIONS = [
  { section: "workspaces", label: "New workspace", icon: FolderGit2 },
  { section: "workflows", label: "New workflow", icon: Workflow },
  { section: "steps", label: "New plan", icon: ListChecks },
  { section: "live", label: "New live session", icon: Radio },
  { section: "agents", label: "New agent", icon: Bot },
  { section: "topics", label: "New topic", icon: MessagesSquare },
  { section: "chats", label: "New chat", icon: MessageSquare },
] satisfies { section: CreateSection; label: string; icon: typeof Plus }[];

function clampPosition(position: Position): Position {
  return {
    right: Math.max(MARGIN, Math.min(position.right, window.innerWidth - SIZE - MARGIN)),
    bottom: Math.max(MARGIN, Math.min(position.bottom, window.innerHeight - SIZE - MARGIN)),
  };
}

function readPosition(): Position {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved: unknown = JSON.parse(raw);
      if (saved && typeof saved === "object" && "right" in saved && "bottom" in saved
        && typeof saved.right === "number" && Number.isFinite(saved.right)
        && typeof saved.bottom === "number" && Number.isFinite(saved.bottom)) {
        return clampPosition({ right: saved.right, bottom: saved.bottom });
      }
    }
  } catch (error) {
    console.warn("Could not restore the quick-actions position.", error);
  }
  return clampPosition(DEFAULT_POSITION);
}

function savePosition(position: Position): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(position));
  } catch (error) {
    console.warn("Could not save the quick-actions position.", error);
  }
}

interface Props {
  agentsEnabled: boolean;
  liveEnabled: boolean;
  onCreate: (section: CreateSection) => void;
}

export function QuickActions({ agentsEnabled, liveEnabled, onCreate }: Props) {
  const [position, setPosition] = useState(readPosition);
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [layout, setLayout] = useState({ above: true, height: 360, left: -188, width: 240, bendsLeft: true });
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drag = useRef<{ x: number; y: number; position: Position; current: Position; moved: boolean } | null>(null);
  const endDrag = useRef<(() => void) | null>(null);
  const suppressClick = useRef(false);
  const pendingFocus = useRef<number | null>(null);
  const menuId = useId();
  const helpId = useId();
  const actions = ACTIONS.filter(({ section }) =>
    (liveEnabled || section !== "live")
    && (agentsEnabled || !["agents", "workflows"].includes(section)),
  );

  function cancelClose(): void {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  }

  useLayoutEffect(() => {
    const above = Math.max(0, window.innerHeight - position.bottom - SIZE - GAP - MARGIN);
    const below = Math.max(0, position.bottom - GAP - MARGIN);
    const fitsAbove = (menuRef.current?.scrollHeight ?? 0) <= above;
    const opensAbove = fitsAbove || above >= below;
    const buttonLeft = window.innerWidth - position.right - SIZE;
    const bendsLeft = buttonLeft > (window.innerWidth - SIZE) / 2;
    const width = Math.min(240, bendsLeft ? buttonLeft + SIZE - MARGIN : window.innerWidth - buttonLeft - MARGIN);
    const left = bendsLeft ? SIZE - width : 0;
    setLayout({ above: opensAbove, height: opensAbove ? above : below, left, width, bendsLeft });
  }, [position, open, actions.length]);

  useLayoutEffect(() => {
    if (!open || pendingFocus.current === null) return;
    menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[pendingFocus.current]?.focus();
    pendingFocus.current = null;
  }, [open]);

  useEffect(() => {
    function resize(): void {
      setPosition((previous) => clampPosition(previous));
    }
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      if (closeTimer.current) clearTimeout(closeTimer.current);
      endDrag.current?.();
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function outside(event: globalThis.PointerEvent): void {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) setOpen(false);
    }
    function escape(event: globalThis.KeyboardEvent): void {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("pointerdown", outside);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", outside);
      window.removeEventListener("keydown", escape);
    };
  }, [open]);

  function pointerDown(event: PointerEvent<HTMLButtonElement>): void {
    if (event.button !== 0 || !event.isPrimary) return;
    cancelClose();
    suppressClick.current = false;
    drag.current = { x: event.clientX, y: event.clientY, position, current: position, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function pointerMove(event: PointerEvent<HTMLButtonElement>): void {
    const current = drag.current;
    if (!current) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    if (!current.moved && Math.hypot(dx, dy) < 6) return;
    if (!current.moved) {
      current.moved = true;
      endDrag.current = beginDrag();
      setDragging(true);
      setOpen(false);
    }
    current.current = clampPosition({ right: current.position.right - dx, bottom: current.position.bottom - dy });
    setPosition(current.current);
  }

  function pointerEnd(): void {
    if (!drag.current) return;
    suppressClick.current = drag.current.moved;
    if (drag.current.moved) savePosition(drag.current.current);
    drag.current = null;
    endDrag.current?.();
    endDrag.current = null;
    setDragging(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (event.altKey && event.target === triggerRef.current) {
      const delta = event.shiftKey ? 8 : 24;
      const offsets: Record<string, Position> = {
        ArrowLeft: { right: delta, bottom: 0 },
        ArrowRight: { right: -delta, bottom: 0 },
        ArrowUp: { right: 0, bottom: delta },
        ArrowDown: { right: 0, bottom: -delta },
      };
      const offset = offsets[event.key];
      if (offset || event.key === "Home") {
        event.preventDefault();
        const next = clampPosition(offset
          ? { right: position.right + offset.right, bottom: position.bottom + offset.bottom }
          : DEFAULT_POSITION);
        setPosition(next);
        savePosition(next);
        return;
      }
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      setOpen(true);
      const buttons = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []);
      const index = buttons.findIndex((button) => button === document.activeElement);
      const last = buttons.length - 1;
      const next = event.key === "Home" ? 0 : event.key === "End" ? last
        : event.key === "ArrowDown" ? (index + 1) % buttons.length
        : index <= 0 ? last : index - 1;
      if (open) buttons[next]?.focus();
      else pendingFocus.current = next;
    }
  }

  return (
    <div
      ref={rootRef}
      className={`quick-actions fixed ${Z_INDEX.POPOVER}`}
      style={{ right: position.right, bottom: position.bottom }}
      data-open={open}
      data-dragging={dragging}
      onKeyDown={onKeyDown}
      onPointerEnter={(event) => {
        cancelClose();
        if (event.pointerType === "mouse" && !drag.current && !suppressClick.current) setOpen(true);
      }}
      onPointerLeave={() => {
        cancelClose();
        closeTimer.current = setTimeout(() => {
          if (!rootRef.current?.contains(document.activeElement)) setOpen(false);
        }, 140);
      }}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <div
        className="quick-actions-popover absolute"
        data-side={layout.above ? "top" : "bottom"}
        data-bend={layout.bendsLeft ? "left" : "right"}
        style={{
          left: layout.left,
          width: layout.width,
          ...(layout.above ? { bottom: "100%", paddingBottom: GAP } : { top: "100%", paddingTop: GAP }),
        }}
        inert={!open}
        aria-hidden={!open}
      >
        <div ref={menuRef} id={menuId} role="menu" aria-label="Create new" className="quick-actions-menu" style={{ maxHeight: layout.height }}>
          {actions.map(({ section, label, icon: Icon }, index) => {
            const distance = (layout.above ? actions.length - index : index + 1) * 50 + 14;
            const curve = ARC_DEPTH * (distance / (actions.length * 50 + 14)) ** 2;
            return (
              <div
                key={section}
                role="none"
                className="quick-actions-slot"
                style={{ transitionDelay: open ? `${(layout.above ? actions.length - 1 - index : index) * 22}ms` : "0ms" }}
              >
                <button
                  role="menuitem"
                  type="button"
                  aria-label={label}
                  className={`quick-actions-item ${sectionColor(section).accentText}`}
                  style={layout.bendsLeft ? { right: curve } : { left: curve }}
                  onClick={() => {
                    setOpen(false);
                    onCreate(section);
                  }}
                >
                  <span className="quick-actions-pill" style={{ maxWidth: layout.width - curve - 8 }} aria-hidden="true">
                    <span className="quick-actions-label">{label}</span>
                  </span>
                  <span className="quick-actions-icon"><Icon size={19} strokeWidth={1.8} aria-hidden="true" /></span>
                </button>
              </div>
            );
          })}
        </div>
      </div>
      <button
        ref={triggerRef}
        type="button"
        className="quick-actions-trigger flex h-[52px] w-[52px] touch-none select-none items-center justify-center rounded-full"
        aria-label="Quick actions"
        aria-describedby={helpId}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onPointerDown={pointerDown}
        onPointerMove={pointerMove}
        onPointerUp={pointerEnd}
        onPointerCancel={pointerEnd}
        onLostPointerCapture={pointerEnd}
        onClick={() => {
          if (suppressClick.current) {
            suppressClick.current = false;
            return;
          }
          setOpen((previous) => !previous);
        }}
      >
        <img src="/logo.svg" alt="" width={42} height={42} draggable={false} />
        <span className="quick-actions-badge" aria-hidden="true"><Plus size={11} strokeWidth={2.5} /></span>
      </button>
      <span id={helpId} className="sr-only">
        Hover or click to create. Drag to move, or use Alt and arrow keys. Alt and Home resets the position.
      </span>
    </div>
  );
}
