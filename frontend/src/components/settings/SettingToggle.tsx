import type { ReactNode } from "react";

/** A labelled checkbox with a muted explanation underneath. */
export function SettingToggle({
  checked,
  onChange,
  disabled,
  label,
  children,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  label: string;
  children: ReactNode;
}) {
  return (
    <label className={`flex items-start gap-2 ${disabled ? "opacity-50" : "cursor-pointer"}`}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-accent"
      />
      <span>
        <span className="block text-sm">{label}</span>
        <span className="block text-[11px] text-muted">{children}</span>
      </span>
    </label>
  );
}
