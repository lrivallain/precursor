import { ListChecks } from "lucide-react";
import { Markdown } from "../Markdown";

/** The note a topic keeps when a step plan is spawned from it (Markdown with a plan link). */
export function StepPlanTrace({ content }: { content: string }) {
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="inline-flex max-w-full items-start gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.06] px-3 py-1.5 text-sm text-fg">
        <ListChecks size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        <Markdown className="min-w-0 break-words text-sm">{content}</Markdown>
      </div>
    </div>
  );
}
