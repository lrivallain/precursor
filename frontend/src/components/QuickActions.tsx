import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, PointerEvent } from "react";
import { Bot, FolderGit2, ListChecks, MessageSquare, MessagesSquare, Plus, Radio, Workflow } from "lucide-react";
import type { CoreSidebarMode } from "./Sidebar";
import { Z_INDEX } from "../lib/constants";
import { beginDrag } from "../lib/dragGuard";

type CreateSection = Exclude<CoreSidebarMode, "iq">;
type Position = { right: number; bottom: number };

const STORAGE_KEY = "precursor.quickActions.position";
const SIZE = 52;
const MARGIN = 16;
// Dock geometry: a column of tiles centred on the launcher, a small gap away from it.
const TILE = 40;
const ITEM_GAP = 8;
const PAD = 8;
const COLUMN = 64;
const INSET = (COLUMN - SIZE) / 2;
const LAUNCHER_GAP = 12;
// Fisheye magnification, as in the macOS Dock: neighbours grow too, and the dock lengthens.
const MAGNIFY = 1.45;
const RANGE = 110;
const BULGE = Math.ceil(TILE * (MAGNIFY - 1));
const MAX_GROWTH = 56;
const DEFAULT_POSITION: Position = { right: 24, bottom: 24 };
// Listed from the far end of the dock to the launcher. Glyph colours are the section's 400 (dark) / 600 (light) shades.
const ACTIONS = [
  { section: "workspaces", label: "New workspace", icon: FolderGit2, colors: ["#fbbf24", "#d97706"] },
  { section: "workflows", label: "New workflow", icon: Workflow, colors: ["#818cf8", "#4f46e5"] },
  { section: "steps", label: "New plan", icon: ListChecks, colors: ["#2dd4bf", "#0d9488"] },
  { section: "live", label: "New live session", icon: Radio, colors: ["#fb7185", "#e11d48"] },
  { section: "agents", label: "New agent", icon: Bot, colors: ["#a78bfa", "#7c3aed"] },
  { section: "topics", label: "New topic", icon: MessagesSquare, colors: ["#38bdf8", "#0284c7"] },
  { section: "chats", label: "New chat", icon: MessageSquare, colors: ["#34d399", "#059669"] },
] satisfies { section: CreateSection; label: string; icon: typeof Plus; colors: [string, string] }[];

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

/**
 * Tile scales, indexed outward from the launcher, for a pointer `offset` px from
 * the dock's launcher-side edge. Scales are solved in un-magnified space so the
 * tile under the pointer stays under it as its neighbours grow, without jitter.
 * `exact` treats the offset as already un-magnified (keyboard focus).
 */
function fisheye(offset: number, count: number, exact = false): number[] {
  const scalesAt = (point: number) => Array.from({ length: count }, (_, index) => {
    const distance = Math.abs(point - (PAD + index * (TILE + ITEM_GAP) + TILE / 2)) / RANGE;
    return distance >= 1 ? 1 : 1 + (MAGNIFY - 1) * (Math.cos(distance * Math.PI) + 1) / 2;
  });
  if (exact) return scalesAt(offset);
  const project = (point: number, scales: number[]): number => {
    let actual = PAD;
    for (let index = 0; index < count; index++) {
      const start = PAD + index * (TILE + ITEM_GAP);
      if (point < start) return actual - (start - point);
      if (point <= start + TILE) return actual + (point - start) * scales[index];
      actual += TILE * scales[index] + ITEM_GAP;
    }
    return actual + point - (PAD + count * (TILE + ITEM_GAP));
  };
  let point = offset;
  for (let step = 0; step < 4; step++) point -= project(point, scalesAt(point)) - offset;
  return scalesAt(point);
}

interface Props {
  agentsEnabled: boolean;
  liveEnabled: boolean;
  /** The section on screen, marked with a Dock-style dot; null at home. */
  current?: string | null;
  onCreate: (section: CreateSection) => void;
}

export function QuickActions({ agentsEnabled, liveEnabled, current = null, onCreate }: Props) {
  const [position, setPosition] = useState(readPosition);
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [layout, setLayout] = useState({ above: true, height: 360, bendsLeft: true });
  const [scales, setScales] = useState<number[] | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLSpanElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drag = useRef<{ x: number; y: number; position: Position; current: Position; moved: boolean } | null>(null);
  const endDrag = useRef<(() => void) | null>(null);
  const suppressClick = useRef(false);
  const pendingFocus = useRef<number | null>(null);
  const menuId = useId();
  const helpId = useId();
  const available = ACTIONS.filter(({ section }) =>
    (liveEnabled || section !== "live")
    && (agentsEnabled || !["agents", "workflows"].includes(section)),
  );
  // Visual (and keyboard) order runs top to bottom; the launcher end is always "New chat".
  const actions = layout.above ? available : [...available].reverse();
  const outward = (index: number) => (layout.above ? actions.length - 1 - index : index);
  const baseHeight = actions.length * TILE + (actions.length - 1) * ITEM_GAP + 2 * PAD;
  const canMagnify = baseHeight + MAX_GROWTH <= layout.height;

  function cancelClose(): void {
    if (closeTimer.current) clearTimeout(closeTimer.current);
    closeTimer.current = null;
  }

  function resetMagnification(): void {
    setScales(null);
    setActive(null);
  }

  useLayoutEffect(() => {
    const above = Math.max(0, window.innerHeight - position.bottom - SIZE - LAUNCHER_GAP - MARGIN);
    const below = Math.max(0, position.bottom - LAUNCHER_GAP - MARGIN);
    const fitsAbove = (menuRef.current?.scrollHeight ?? 0) <= above;
    const opensAbove = fitsAbove || above >= below;
    const buttonLeft = window.innerWidth - position.right - SIZE;
    const bendsLeft = buttonLeft > (window.innerWidth - SIZE) / 2;
    setLayout({ above: opensAbove, height: opensAbove ? above : below, bendsLeft });
  }, [position, open, available.length]);

  useLayoutEffect(() => {
    if (!open || pendingFocus.current === null) return;
    menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')[pendingFocus.current]?.focus();
    pendingFocus.current = null;
  }, [open]);

  useLayoutEffect(() => {
    if (!open) resetMagnification();
  }, [open]);

  function leaveLabel(event: PointerEvent<HTMLElement>): void {
    // Moving between a tile and its label pill keeps both active.
    const next = event.relatedTarget;
    if (next instanceof Node && (menuRef.current?.contains(next) || labelsRef.current?.contains(next))) return;
    resetMagnification();
  }

  function magnifyAt(event: PointerEvent<HTMLDivElement>): void {
    const menu = menuRef.current;
    if (!menu || event.pointerType !== "mouse" || !canMagnify) return;
    const box = menu.getBoundingClientRect();
    const offset = layout.above ? box.bottom - event.clientY : event.clientY - box.top;
    setScales(fisheye(offset, actions.length));
  }

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
        ref={popoverRef}
        className="quick-actions-popover absolute"
        data-side={layout.above ? "top" : "bottom"}
        data-bend={layout.bendsLeft ? "left" : "right"}
        style={{
          left: layout.bendsLeft ? -INSET - BULGE : -INSET,
          width: COLUMN + BULGE,
          ...(layout.above
            ? { bottom: -INSET, paddingBottom: INSET + SIZE + LAUNCHER_GAP }
            : { top: -INSET, paddingTop: INSET + SIZE + LAUNCHER_GAP }),
        }}
        inert={!open}
        aria-hidden={!open}
      >
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label="Create new"
          className="quick-actions-menu"
          style={{
            maxHeight: layout.height,
            padding: `${PAD}px ${layout.bendsLeft ? (COLUMN - TILE) / 2 : (COLUMN - TILE) / 2 + BULGE}px ${PAD}px ${layout.bendsLeft ? (COLUMN - TILE) / 2 + BULGE : (COLUMN - TILE) / 2}px`,
          }}
          onPointerMove={magnifyAt}
          onPointerLeave={leaveLabel}
          onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
        >
          {actions.map(({ section, label, icon: Icon, colors }, index) => {
            const scale = scales?.[outward(index)] ?? 1;
            return (
              <button
                key={section}
                role="menuitem"
                type="button"
                aria-label={label}
                className="quick-actions-item"
                data-active={active === index}
                style={{
                  width: TILE * scale,
                  height: TILE * scale,
                  "--qa-from": colors[0],
                  "--qa-to": colors[1],
                  "--qa-delay": open ? `${outward(index) * 24}ms` : "0ms",
                } as CSSProperties}
                onPointerEnter={() => setActive(index)}
                onFocus={(event) => {
                  setActive(index);
                  if (canMagnify && event.currentTarget.matches(":focus-visible")) {
                    setScales(fisheye(PAD + outward(index) * (TILE + ITEM_GAP) + TILE / 2, actions.length, true));
                  }
                }}
                onBlur={(event) => {
                  const next = event.relatedTarget;
                  if (!(next instanceof Element && next.matches('[role="menuitem"]'))) resetMagnification();
                }}
                onClick={() => {
                  setOpen(false);
                  onCreate(section);
                }}
              >
                <span className="quick-actions-tile" aria-hidden="true"><Icon strokeWidth={1.9} /></span>
                {section === current && <span className="quick-actions-dot" aria-hidden="true" />}
              </button>
            );
          })}
        </div>
        {/* One pill per tile, outside the scrolling list so it is never clipped. Each sits behind its
            tile, at the tile's target size and position, and rolls out of it (or back in) with the
            same timing as the magnification, so the icon grows while its name unrolls. */}
        <span ref={labelsRef} className="quick-actions-labels">
          {actions.map(({ section, label, colors }, index) => {
            const sizes = actions.map((_, other) => TILE * (scales?.[outward(other)] ?? 1));
            const size = sizes[index];
            const top = (layout.above ? 0 : INSET + SIZE + LAUNCHER_GAP) + PAD - scrollTop
              + sizes.slice(0, index).reduce((sum, other) => sum + other + ITEM_GAP, 0);
            return (
              <span
                key={section}
                className="quick-actions-tip"
                data-open={active === index}
                style={{
                  top,
                  height: size,
                  [layout.bendsLeft ? "right" : "left"]: (COLUMN - TILE) / 2,
                  [layout.bendsLeft ? "paddingRight" : "paddingLeft"]: size + 2,
                  "--qa-size": `${size}px`,
                  "--qa-from": colors[0],
                  "--qa-to": colors[1],
                } as CSSProperties}
                aria-hidden="true"
                onPointerLeave={leaveLabel}
                // Keep focus where it is: a blur to <body> would close the menu before the click lands.
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => {
                  setOpen(false);
                  onCreate(section);
                }}
              >
                <span className="quick-actions-label">{label}</span>
              </span>
            );
          })}
        </span>
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
