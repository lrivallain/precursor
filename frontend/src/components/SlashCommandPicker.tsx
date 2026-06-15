import { useEffect, useRef } from "react";
import type { SlashCommand } from "../lib/commands";

interface Props {
  commands: SlashCommand[];
  activeIndex: number;
  onSelect: (cmd: SlashCommand) => void;
  onHover: (index: number) => void;
}

/**
 * Popover list shown above the composer when the user is typing a slash
 * command. The composer owns keyboard navigation; this component just
 * renders the list and forwards mouse events.
 */
export function SlashCommandPicker({
  commands,
  activeIndex,
  onSelect,
  onHover,
}: Props) {
  const activeRef = useRef<HTMLLIElement>(null);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (commands.length === 0) return null;

  return (
    <div
      role="listbox"
      aria-label="Slash commands"
      className="absolute bottom-full left-0 right-0 mb-1 max-h-64 overflow-y-auto bg-surface border border-border rounded shadow-lg z-20"
    >
      <ul className="py-1">
        {commands.map((cmd, i) => {
          const active = i === activeIndex;
          return (
            <li
              key={cmd.name}
              ref={active ? activeRef : undefined}
              role="option"
              aria-selected={active}
              onMouseEnter={() => onHover(i)}
              onMouseDown={(e) => {
                // Prevent textarea blur before click handler fires.
                e.preventDefault();
                onSelect(cmd);
              }}
              className={`px-3 py-1.5 cursor-pointer ${
                active ? "bg-bg" : "hover:bg-bg/60"
              }`}
            >
              <div className="flex items-baseline gap-2">
                <code className="text-xs font-mono text-accent">{cmd.label}</code>
                {cmd.kind === "skill" && (
                  <span className="text-[10px] uppercase tracking-wide text-accent/80 border border-accent/30 rounded px-1">
                    skill
                  </span>
                )}
                {cmd.argumentHint && (
                  <span className="text-[11px] text-muted truncate">
                    {cmd.argumentHint}
                  </span>
                )}
              </div>
              <div className="text-[11px] text-muted leading-snug">
                {cmd.description}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="px-3 py-1 border-t border-border text-[10px] text-muted">
        ↑↓ navigate · Tab/Enter to select · Esc to dismiss
      </div>
    </div>
  );
}
