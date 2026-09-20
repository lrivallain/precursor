interface Props {
  checked: boolean;
  onChange: (checked: boolean) => void;
}

export function WorkflowAgentFilter({ checked, onChange }: Props) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-3.5 w-3.5 accent-accent"
      />
      Show workflow agents
    </label>
  );
}
