import { useEffect, useRef, useState } from "react";
import { Brain, ChevronDown, ChevronRight } from "lucide-react";
import { Markdown } from "./Markdown";

interface Props {
  reasoning: string;
  /** The model is still thinking: the header pulses and follows the latest step. */
  live?: boolean;
  className?: string;
}

/**
 * The model's thinking for one assistant round, collapsed by default. While
 * collapsed the header previews the latest step, so the user can follow along
 * without opening it.
 */
export function ReasoningDisclosure({ reasoning, live = false, className = "" }: Props) {
  const [open, setOpen] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const step = latestThinkingStep(reasoning);

  // Follow new thinking while it streams, like the transcript follows the reply.
  useEffect(() => {
    const el = bodyRef.current;
    if (open && live && el) el.scrollTop = el.scrollHeight;
  }, [open, live, reasoning]);

  return (
    <div className={`min-w-0 text-xs text-muted ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full min-w-0 cursor-pointer items-center gap-1.5 text-left hover:text-text"
      >
        {open ? (
          <ChevronDown size={12} className="shrink-0" />
        ) : (
          <ChevronRight size={12} className="shrink-0" />
        )}
        <Brain
          size={12}
          className={`shrink-0 text-cyan-600 dark:text-cyan-300 ${live ? "animate-pulse" : ""}`}
        />
        <span className="shrink-0 font-medium">{live ? "Thinking…" : "Thinking"}</span>
        {!open && step && <span className="min-w-0 truncate italic">{step}</span>}
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="mt-1 mb-2 ml-1.5 max-h-72 overflow-y-auto border-l-2 border-cyan-500/30 pl-3"
        >
          <Markdown className="text-xs leading-relaxed text-muted">{reasoning}</Markdown>
        </div>
      )}
    </div>
  );
}

/**
 * The step the model is on: its latest summary heading (reasoning summaries open
 * each step with a bold or `#` title), else the last line of its thinking.
 */
export function latestThinkingStep(reasoning: string): string {
  const lines = reasoning
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    const heading = lines[i].match(/^\*\*(.+?)\*\*$/) ?? lines[i].match(/^#{1,6}\s+(.+)$/);
    if (heading) return heading[1].trim();
  }
  return (lines[lines.length - 1] ?? "").replace(/[*_`#>]/g, "").trim();
}
