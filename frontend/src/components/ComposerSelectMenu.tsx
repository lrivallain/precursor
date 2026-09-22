import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, Search } from "lucide-react";

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
  const selectedRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const setOpen = (next: boolean): void => {
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  };

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
      if (!rootRef.current?.contains(e.target as Node | null)) close();
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") close();
    }
    document.addEventListener("pointerdown", onDocPointerDown);
    document.addEventListener("keydown", onKeyDown);
    searchRef.current?.focus();
    // Bring the active row into view when the menu opens.
    selectedRef.current?.scrollIntoView({ block: "nearest" });
    return () => {
      document.removeEventListener("pointerdown", onDocPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange]);

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

  function selectFirstMatch(): void {
    const first = visibleGroups.flatMap((g) => g.options)[0];
    if (!first) return;
    onSelect(first.value);
    setOpen(false);
  }

  return (
    <div ref={rootRef} className="relative">
      <button
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
        className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-muted hover:text-text hover:bg-bg outline-none disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {icon}
        <span className="max-w-[13rem] truncate">{triggerLabel}</span>
        <ChevronDown size={13} className="shrink-0 opacity-70" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={ariaLabel}
          className={`absolute bottom-full left-0 z-30 mb-2 max-w-[20rem] rounded-xl border border-border bg-surface p-1 shadow-xl ${menuMinWidthClass}`}
        >
          {filterPlaceholder && (
            <div className="flex items-center gap-1.5 border-b border-border px-2 pb-1.5 pt-1">
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
                  } else if (e.key !== "Escape") {
                    // Keep typing away from the composer's global hotkeys.
                    e.stopPropagation();
                  }
                }}
                className="w-full bg-transparent text-sm text-text placeholder:text-muted outline-none"
              />
            </div>
          )}
          <div className="max-h-72 overflow-y-auto">
            {!hasOptions && (
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
          </div>
        </div>
      )}
    </div>
  );
}
