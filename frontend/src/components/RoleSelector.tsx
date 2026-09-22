import { useEffect, useRef } from "react";
import { Check, ChevronDown, Drama } from "lucide-react";
import { useRoles } from "../lib/rolesStore";
import { ComposerSelectMenu } from "./ComposerSelectMenu";

interface Props {
  /** Currently assigned role id on the discussion (null = default). */
  value: number | null;
  onChange: (roleId: number | null) => void;
  /** Controlled open state so `/role` (no args) can pop it open. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** "composer" (default) is the borderless pill that sits in a composer
   *  toolbar beside the model and context pickers — rendered by the very same
   *  control they are. "toolbar" is the bordered pill that opens downward,
   *  matching the picker row above the Live transcript. */
  variant?: "toolbar" | "composer";
  /** Greys the trigger out and refuses to open it (create surfaces disable
   *  their controls while busy or while the runtime is unavailable). */
  disabled?: boolean;
}

export function RoleSelector({
  value,
  onChange,
  open,
  onOpenChange,
  variant = "composer",
  disabled = false,
}: Props) {
  const roles = useRoles();
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open || variant !== "toolbar") return;
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
  }, [open, onOpenChange, variant]);

  const defaultRole = roles.find((r) => r.is_default);
  // A null/unknown role_id resolves to the default role server-side.
  const selected = roles.find((r) => r.id === value) ?? defaultRole ?? null;
  const label = selected?.name ?? "default";

  function choose(roleId: number | null) {
    onOpenChange(false);
    onChange(roleId);
  }

  if (variant === "composer") {
    return (
      <ComposerSelectMenu
        ariaLabel="Assistant role"
        tooltip="Assistant role — the persona this conversation starts with"
        triggerLabel={label}
        // "default" on its own would read as a sibling of "Default ctx", so
        // the role pill keeps a glyph the model/context pills don't need.
        icon={<Drama size={13} className="shrink-0 opacity-70" />}
        value={String(selected?.id ?? "")}
        groups={[
          {
            options: roles.map((r) => ({
              value: String(r.id),
              label: r.name,
              description: r.system_prompt || undefined,
            })),
          },
        ]}
        disabled={disabled}
        open={open}
        onOpenChange={onOpenChange}
        onSelect={(v) => {
          const role = roles.find((r) => String(r.id) === v);
          choose(role && !role.is_default ? role.id : null);
        }}
      />
    );
  }

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        data-tooltip={`Assistant role: ${label}`}
        aria-label={`Assistant role: ${label}. Click to change.`}
        className="flex max-w-[15rem] items-center gap-1 rounded border border-border bg-surface px-2 py-1 text-[11px] text-text outline-none focus:border-accent disabled:opacity-60"
      >
        <Drama size={11} className="shrink-0 text-muted" />
        <span className="flex-1 truncate text-left">{label}</span>
        <ChevronDown size={11} className="shrink-0 text-muted" />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Select assistant role"
          className="absolute left-0 top-full mt-1 z-40 min-w-[12rem] max-w-[16rem] rounded-md border border-border bg-bg shadow-lg py-1 text-sm max-h-[60vh] overflow-y-auto"
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
                    <span className="block truncate text-[11px] text-muted">
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
