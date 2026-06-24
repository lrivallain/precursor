import { MessageSquarePlus } from "lucide-react";

interface Props {
  items: string[];
  /** Send the chosen suggestion as the next user turn. */
  onPick: (text: string) => void;
  /** Disable while a turn is in flight so picks can't queue mid-stream. */
  disabled?: boolean;
  /** Extra classes for the outer container. */
  className?: string;
}

/**
 * Clickable follow-up chips rendered under the latest assistant turn. The model
 * proposes these via a trailing `suggest` block; picking one sends it verbatim
 * as the user's next message. Shared by topics, chats, workspaces, and agents.
 */
export function SuggestedReplies({ items, onPick, disabled, className }: Props) {
  if (items.length === 0) return null;
  return (
    <div className={`flex flex-col items-start gap-1.5${className ? ` ${className}` : ""}`}>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-muted">
        <MessageSquarePlus size={11} />
        Suggested replies
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((text) => (
          <button
            key={text}
            type="button"
            disabled={disabled}
            onClick={() => onPick(text)}
            className="rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-sm text-text transition hover:bg-accent/20 hover:border-accent/60 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}
