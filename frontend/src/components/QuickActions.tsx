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
// Magnification, as in the macOS Dock: the tile under the pointer stays under it while its
// neighbours grow on both sides along a Gaussian curve. The pointer and the overall strength
// follow critically damped springs, so the column moves continuously and never overshoots.
const MAGNIFY = 1.5;
const RANGE = 100;
const POINTER_STIFFNESS = 320;
const STRENGTH_STIFFNESS = 220;
const BULGE = Math.ceil(TILE * (MAGNIFY - 1));
const MAX_GROWTH = 72;
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

type Spring = { x: number; v: number };

function settle(spring: Spring, target: number, stiffness: number, dt: number): void {
  spring.v += (stiffness * (target - spring.x) - 2 * Math.sqrt(stiffness) * spring.v) * dt;
  spring.x += spring.v * dt;
}

/** Resting centre of the tile `index` places out from the launcher, measured from the column's edge. */
function restingCentre(index: number): number {
  return PAD + index * (TILE + ITEM_GAP) + TILE / 2;
}

/** Tile scales, indexed outward from the launcher, for a pointer at `point` (resting coordinates). */
function magnifiedScales(point: number, strength: number, count: number): number[] {
  return Array.from({ length: count }, (_, index) => {
    const distance = (point - restingCentre(index)) / (RANGE * 0.42);
    return 1 + (MAGNIFY - 1) * strength * Math.exp(-0.5 * distance * distance);
  });
}

/** Where resting point `point` lands once tiles take `scales`, with the column anchored at its edge. */
function magnifiedPoint(point: number, scales: number[]): number {
  let actual = PAD;
  for (let index = 0; index < scales.length; index++) {
    const start = PAD + index * (TILE + ITEM_GAP);
    if (point < start) return actual - (start - point);
    if (point <= start + TILE) return actual + (point - start) * scales[index];
    actual += TILE * scales[index] + ITEM_GAP;
  }
  return actual + point - (PAD + scales.length * (TILE + ITEM_GAP));
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
  const [active, setActive] = useState<number | null>(null);
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
  // Magnification runs outside React: a frame loop writes tile and label geometry directly.
  const magnify = useRef({
    target: null as number | null,
    pointer: { x: 0, v: 0 } as Spring,
    strength: { x: 0, v: 0 } as Spring,
    frame: 0,
    last: 0,
  });
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

  /** Size the tiles and position their label pills for the given scales (outward order). */
  function applyMagnification(scales: number[], shift: number): void {
    const menu = menuRef.current;
    const labels = labelsRef.current;
    if (!menu || !labels) return;
    const items = menu.querySelectorAll<HTMLElement>('[role="menuitem"]');
    const pills = labels.children;
    // Growing on both sides pulls the column toward the launcher by `shift` (at most PAD).
    const edge = `${PAD + shift}px`;
    if (layout.above) menu.style.paddingBottom = edge;
    else menu.style.paddingTop = edge;
    let top = (layout.above ? 0 : INSET + SIZE + LAUNCHER_GAP) + (layout.above ? PAD : PAD + shift) - menu.scrollTop;
    items.forEach((item, index) => {
      const size = TILE * (scales[outward(index)] ?? 1);
      item.style.width = `${size}px`;
      item.style.height = `${size}px`;
      const pill = pills[index] as HTMLElement | undefined;
      if (pill) {
        pill.style.top = `${top}px`;
        pill.style.height = `${size}px`;
        pill.style.paddingRight = layout.bendsLeft ? `${size + 2}px` : "";
        pill.style.paddingLeft = layout.bendsLeft ? "" : `${size + 2}px`;
        pill.style.setProperty("--qa-size", `${size}px`);
      }
      top += size + ITEM_GAP;
    });
  }

  function step(now: number): void {
    const state = magnify.current;
    const dt = Math.min(0.05, (now - state.last) / 1000);
    state.last = now;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const goal = state.target === null ? 0 : 1;
    if (state.target !== null && state.strength.x < 0.03) state.pointer = { x: state.target, v: 0 };
    if (reduced) {
      state.strength = { x: goal, v: 0 };
      if (state.target !== null) state.pointer = { x: state.target, v: 0 };
    } else {
      for (let elapsed = 0; elapsed < dt; elapsed += 1 / 240) {
        const slice = Math.min(1 / 240, dt - elapsed);
        settle(state.strength, goal, STRENGTH_STIFFNESS, slice);
        if (state.target !== null) settle(state.pointer, state.target, POINTER_STIFFNESS, slice);
      }
    }
    const strength = Math.max(0, state.strength.x);
    const scales = magnifiedScales(state.pointer.x, strength, actions.length);
    const shift = Math.max(-PAD, Math.min(0, state.pointer.x - magnifiedPoint(state.pointer.x, scales)));
    applyMagnification(scales, shift);
    const resting = Math.abs(state.strength.x - goal) < 1e-3 && Math.abs(state.strength.v) < 1e-3
      && (state.target === null || (Math.abs(state.pointer.x - state.target) < 0.05 && Math.abs(state.pointer.v) < 0.05));
    state.frame = resting ? 0 : requestAnimationFrame((time) => stepRef.current(time));
  }
  // Frames always run the latest render's step, so a layout change mid-animation is picked up.
  const stepRef = useRef(step);
  stepRef.current = step;

  /** Aim the magnification at `point` (resting coordinates), or release it with null. */
  function magnifyTo(point: number | null): void {
    const state = magnify.current;
    state.target = canMagnify ? point : null;
    if (!state.frame) {
      state.last = performance.now();
      state.frame = requestAnimationFrame((time) => stepRef.current(time));
    }
  }

  function resetMagnification(): void {
    magnifyTo(null);
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
    if (open) return;
    const state = magnify.current;
    cancelAnimationFrame(state.frame);
    Object.assign(state, { target: null, frame: 0, pointer: { x: 0, v: 0 }, strength: { x: 0, v: 0 } });
    applyMagnification([], 0);
    setActive(null);
  }, [open]);

  // Lay the resting geometry out again whenever the column itself changes.
  useLayoutEffect(() => {
    const state = magnify.current;
    applyMagnification(magnifiedScales(state.pointer.x, Math.max(0, state.strength.x), actions.length), 0);
  }, [layout, actions.length]);

  useEffect(() => () => cancelAnimationFrame(magnify.current.frame), []);

  function leaveLabel(event: PointerEvent<HTMLElement>): void {
    // Moving between a tile and its label pill keeps both active.
    const next = event.relatedTarget;
    if (next instanceof Node && (menuRef.current?.contains(next) || labelsRef.current?.contains(next))) return;
    resetMagnification();
  }

  function magnifyAt(event: PointerEvent<HTMLDivElement>): void {
    const menu = menuRef.current;
    if (!menu || event.pointerType !== "mouse") return;
    const box = menu.getBoundingClientRect();
    magnifyTo(layout.above ? box.bottom - event.clientY : event.clientY - box.top);
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
      style={{ right: position.right, bottom: position.bottom, "--qa-tile": `${TILE}px` } as CSSProperties}
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
            // The launcher-side padding is written by the magnification loop.
            [layout.above ? "paddingTop" : "paddingBottom"]: PAD,
            paddingLeft: layout.bendsLeft ? (COLUMN - TILE) / 2 + BULGE : (COLUMN - TILE) / 2,
            paddingRight: layout.bendsLeft ? (COLUMN - TILE) / 2 : (COLUMN - TILE) / 2 + BULGE,
          }}
          onPointerMove={magnifyAt}
          onPointerLeave={leaveLabel}
          onScroll={() => {
            const state = magnify.current;
            applyMagnification(magnifiedScales(state.pointer.x, Math.max(0, state.strength.x), actions.length), 0);
          }}
        >
          {actions.map(({ section, label, icon: Icon, colors }, index) => {
            return (
              <button
                key={section}
                role="menuitem"
                type="button"
                aria-label={label}
                className="quick-actions-item"
                data-active={active === index}
                style={{
                  // Width and height are written by the magnification loop.
                  "--qa-from": colors[0],
                  "--qa-to": colors[1],
                  "--qa-delay": open ? `${outward(index) * 24}ms` : "0ms",
                } as CSSProperties}
                onPointerEnter={() => setActive(index)}
                onFocus={(event) => {
                  setActive(index);
                  if (event.currentTarget.matches(":focus-visible")) magnifyTo(restingCentre(outward(index)));
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
            tile (the magnification loop keeps its size and position in step) and rolls out of it, or
            back in, as the tile becomes active. */}
        <span ref={labelsRef} className="quick-actions-labels">
          {actions.map(({ section, label, colors }, index) => {
            return (
              <span
                key={section}
                className="quick-actions-tip"
                data-open={active === index}
                style={{
                  [layout.bendsLeft ? "right" : "left"]: (COLUMN - TILE) / 2,
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
