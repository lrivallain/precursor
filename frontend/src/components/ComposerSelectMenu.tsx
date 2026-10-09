import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Plus, Search } from "lucide-react";

export interface MenuOption {
  value: string;
  label: string;
  /** Optional muted second line (e.g. a role's system prompt). */
  description?: string;
}

export interface MenuGroup {
  label?: string;
  options: MenuOption[];
}

/**
 * The compact borderless pill + upward popup used by every composer-toolbar
 * picker (model, reasoning effort, context size, assistant role). Shared so the
 * toolbar reads as one row of identical controls and can't drift apart.
 */
export function ComposerSelectMenu({
  ariaLabel,
  tooltip,
  triggerLabel,
  icon,
  value,
  groups,
  emptyHint,
  menuMinWidthClass = "min-w-[11rem]",
  disabled,
  filterPlaceholder,
  fullWidth = false,
  portal = false,
  onCustomValue,
  open: controlledOpen,
  onOpenChange,
  onOpen,
  onSelect,
}: {
  ariaLabel: string;
  tooltip: string;
  triggerLabel: string;
  /** Optional leading glyph, for pickers whose label alone is ambiguous. */
  icon?: ReactNode;
  value: string;
  groups: MenuGroup[];
  emptyHint?: string;
  menuMinWidthClass?: string;
  disabled: boolean;
  // Passing a placeholder opts the menu into type-to-filter; short menus
  // (reasoning effort, context size) leave it off and stay a plain list.
  filterPlaceholder?: string;
  fullWidth?: boolean;
  /** Escape clipping in scrollable panels; composer menus stay inline. */
  portal?: boolean;
  /** Opt-in manual ids for catalogues that omit a deployment/retired model. */
  onCustomValue?: (value: string) => void;
  /** Optional controlled open state, so a slash command can pop the menu. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  onOpen?: () => void;
  onSelect: (value: string) => void;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [menuPosition, setMenuPosition] = useState<CSSProperties | null>(null);
  const positioned = !portal || menuPosition !== null;

  const setOpen = (next: boolean): void => {
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  };

  useLayoutEffect(() => {
    if (!open || !portal) {
      setMenuPosition(null);
      return;
    }
    const position = (): void => {
      const anchor = triggerRef.current?.getBoundingClientRect();
      if (!anchor) return;
      const width = Math.min(320, Math.max(anchor.width, 288), window.innerWidth - 16);
      const above = anchor.top - 16;
      const below = window.innerHeight - anchor.bottom - 16;
      const upwards = above >= 200 || above >= below;
      setMenuPosition({
        width,
        left: Math.max(8, Math.min(anchor.left, window.innerWidth - width - 8)),
        maxHeight: Math.min(360, Math.max(80, upwards ? above : below)),
        ...(upwards ? { bottom: window.innerHeight - anchor.top + 8 } : { top: anchor.bottom + 8 }),
      });
    };
    position();
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    return () => {
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, [open, portal]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }
    function close(): void {
      setUncontrolledOpen(false);
      onOpenChange?.(false);
    }
    function onDocPointerDown(e: PointerEvent): void {
      const target = e.target as Node | null;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) close();
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") close();
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    document.addEventListener("keydown", onKeyDown);
    if (searchRef.current) searchRef.current.focus();
    else if (portal) (selectedRef.current ?? menuRef.current?.querySelector<HTMLButtonElement>('[role="option"]'))?.focus();
    // Bring the active row into view when the menu opens.
    selectedRef.current?.scrollIntoView({ block: "nearest" });
    return () => {
      document.removeEventListener("pointerdown", onDocPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange, positioned, portal]);

  // A matching group label keeps its whole group, so typing a vendor name
  // ("microsoft") lists every model it publishes.
  const q = query.trim().toLowerCase();
  const visibleGroups = q
    ? groups
        .map((g) => ({
          ...g,
          options: g.label?.toLowerCase().includes(q)
            ? g.options
            : g.options.filter(
                (o) =>
                  o.label.toLowerCase().includes(q) || o.value.toLowerCase().includes(q),
              ),
        }))
        .filter((g) => g.options.length > 0)
    : groups;
  const hasOptions = visibleGroups.some((g) => g.options.length > 0);
  const customValue = query.trim();
  const canUseCustomValue = Boolean(
    onCustomValue && customValue && !groups.some((g) => g.options.some((o) => o.value === customValue)),
  );

  function selectFirstMatch(): void {
    const first = visibleGroups.flatMap((g) => g.options)[0];
    if (first) onSelect(first.value);
    else if (canUseCustomValue) onCustomValue?.(customValue);
    else return;
    setOpen(false);
    if (portal) triggerRef.current?.focus();
  }

  const menu = open && positioned ? (
        <div
          ref={menuRef}
          role="listbox"
          aria-label={ariaLabel}
          style={portal ? menuPosition ?? undefined : undefined}
          onKeyDown={portal ? (event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setOpen(false);
              triggerRef.current?.focus();
            } else if (event.key === "Tab") {
              event.stopPropagation();
              const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('input,button:not([disabled])'));
              const first = controls[0];
              const last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last?.focus();
              } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first?.focus();
              }
            }
          } : undefined}
          className={`${portal ? "fixed z-[60] flex flex-col" : "absolute bottom-full left-0 z-30 mb-2"} max-w-[20rem] rounded-xl border border-border bg-surface p-1 shadow-xl ${menuMinWidthClass}`}
        >
          {filterPlaceholder && (
            <div className="flex shrink-0 items-center gap-1.5 border-b border-border px-2 pb-1.5 pt-1">
              <Search size={13} className="shrink-0 text-muted" />
              <input
                ref={searchRef}
                type="text"
                value={query}
                placeholder={filterPlaceholder}
                aria-label={filterPlaceholder}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    selectFirstMatch();
                  } else if (e.key !== "Escape" && !(portal && e.key === "Tab")) {
                    // Keep typing away from the composer's global hotkeys.
                    e.stopPropagation();
                  }
                }}
                className="w-full bg-transparent text-sm text-text placeholder:text-muted outline-none"
              />
            </div>
          )}
          <div className="max-h-72 min-h-0 overflow-y-auto">
            {!hasOptions && !canUseCustomValue && (
              <div className="px-2 py-1.5 text-xs text-muted">
                {q ? "No match" : (emptyHint ?? "No options")}
              </div>
            )}
            {visibleGroups.map((group, gi) => (
              <div key={group.label ?? gi}>
                {group.label && group.options.length > 0 && (
                  <div className="px-2 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                    {group.label}
                  </div>
                )}
                {group.options.map((opt) => {
                  const selected = opt.value === value;
                  return (
                    <button
                      key={opt.value}
                      ref={selected ? selectedRef : undefined}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      onClick={() => {
                        onSelect(opt.value);
                        setOpen(false);
                        if (portal) triggerRef.current?.focus();
                      }}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-border/50 ${
                        selected ? "text-text" : "text-text/90"
                      }`}
                    >
                      <span className="flex w-4 shrink-0 justify-center self-start pt-0.5">
                        {selected && <Check size={14} className="text-accent" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{opt.label}</span>
                        {opt.description && (
                          <span className="block truncate text-[11px] text-muted">
                            {opt.description}
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
            {canUseCustomValue && (
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => {
                  onCustomValue?.(customValue);
                  setOpen(false);
                  if (portal) triggerRef.current?.focus();
                }}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-border/50"
              >
                <Plus size={14} className="shrink-0 text-accent" aria-hidden="true" />
                <span className="min-w-0 truncate">Use model id "{customValue}"</span>
              </button>
            )}
          </div>
        </div>
  ) : null;

  return (
    // A flex wrapper, so the trigger is a flex item rather than an inline box
    // on a text baseline: an inline-flex box takes its baseline from its first
    // child, so a leading glyph would otherwise sit the pill a fraction of a
    // pixel higher than its icon-less neighbours.
    <div ref={rootRef} className={`relative flex min-w-0 ${fullWidth ? "w-full" : ""}`}>
      <button
        ref={triggerRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        data-tooltip={tooltip}
        disabled={disabled}
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) onOpen?.();
        }}
        className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs hover:text-text hover:bg-bg outline-none disabled:opacity-50 disabled:cursor-not-allowed ${
          fullWidth ? "w-full min-w-0 border border-border bg-bg text-text focus-visible:ring-2 focus-visible:ring-accent" : "text-muted"
        }`}
      >
        {icon}
        <span className={`truncate ${fullWidth ? "min-w-0 flex-1 text-left" : "max-w-[13rem]"}`}>{triggerLabel}</span>
        <ChevronDown size={13} className="shrink-0 opacity-70" />
      </button>
      {portal && menu ? createPortal(menu, document.body) : menu}
    </div>
  );
}
