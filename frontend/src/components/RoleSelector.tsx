import { useEffect, useRef } from "react";
import { Check, Drama } from "lucide-react";
import { useRoles } from "../lib/rolesStore";

interface Props {
  /** Currently assigned role id on the discussion (null = default). */
  value: number | null;
  onChange: (roleId: number | null) => void;
  /** Controlled open state so `/role` (no args) can pop it open. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Which way the menu opens. "up" suits a composer toolbar pinned to the
   *  bottom of the surface; "down" (default) suits a header. */
  placement?: "down" | "up";
  /** Menu edge aligned with the trigger. Defaults to "right" (header controls
   *  sit at the right edge); toolbar controls at the left want "left". */
  align?: "left" | "right";
}

export function RoleSelector({
  value,
  onChange,
  open,
  onOpenChange,
  placement = "down",
  align = "right",
}: Props) {
  const roles = useRoles();
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        onOpenChange(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onOpenChange(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onOpenChange]);

  const defaultRole = roles.find((r) => r.is_default);
  // A null/unknown role_id resolves to the default role server-side.
  const selected =
    roles.find((r) => r.id === value) ?? defaultRole ?? null;
  const label = selected?.name ?? "default";

  function choose(roleId: number | null) {
    onOpenChange(false);
    onChange(roleId);
  }

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-haspopup="menu"
        aria-expanded={open}
        data-tooltip={`Assistant role: ${label}`}
        aria-label={`Assistant role: ${label}. Click to change.`}
        className="flex items-center gap-1.5 max-w-[10rem] px-2 py-1.5 rounded border border-border hover:bg-surface text-sm text-muted hover:text-text"
      >
        <Drama size={15} className="shrink-0" />
        <span className="truncate hidden sm:inline">{label}</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Select assistant role"
          className={`absolute z-40 min-w-[12rem] max-w-[16rem] rounded-md border border-border bg-bg shadow-lg py-1 text-sm max-h-[60vh] overflow-y-auto ${
            align === "left" ? "left-0" : "right-0"
          } ${placement === "up" ? "bottom-full mb-1" : "top-full mt-1"}`}
        >
          {roles.map((r) => {
            const isSelected = selected?.id === r.id;
            return (
              <button
                key={r.id}
                type="button"
                role="menuitemradio"
                aria-checked={isSelected}
                onClick={() => choose(r.is_default ? null : r.id)}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-surface"
              >
                <Check
                  size={14}
                  className={`shrink-0 ${isSelected ? "text-accent" : "opacity-0"}`}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{r.name}</span>
                  {r.system_prompt && (
                    <span className="block text-[11px] text-muted truncate">
                      {r.system_prompt}
                    </span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
