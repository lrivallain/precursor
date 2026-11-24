import { Clock } from "lucide-react";

/**
 * Messages sent while the turn runs, shown where they will land: dimmed user
 * bubbles under the streaming reply. The model reads them at its next step
 * (after the current tool call, or once the answer is written); they then turn
 * into ordinary prompts in the transcript.
 */
export function QueuedMessages({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col items-end gap-1.5" aria-label="Queued messages">
      {items.map((text, i) => (
        <li
          key={i}
          className="max-w-full rounded-lg border border-dashed border-emerald-500/70 bg-emerald-500/5 px-3 py-2 text-sm opacity-75 whitespace-pre-wrap break-words"
        >
          {text}
        </li>
      ))}
      <li
        className="flex items-center gap-1 text-[11px] text-emerald-500"
        data-tooltip="Sent to the running turn: the model reads it after its current step"
      >
        <Clock size={11} aria-hidden />
        queued · read at the next step
      </li>
    </ul>
  );
}
