import { useId, useState } from "react";
import { ListChecks } from "lucide-react";
import { parseStepPlanTrace } from "../../lib/stepPlans";
import { Markdown } from "../Markdown";
import { StepPlanLink } from "./StepPlanNoteHeader";

/** Creation snapshots are plain text: prompt syntax must never become metadata or markup. */
export function StepPlanTrace({ content }: { content: string }) {
  const trace = parseStepPlanTrace(content);
  const [expanded, setExpanded] = useState(false);
  const promptId = useId();
  if (trace) {
    const prompt = trace.snapshot?.prompt;
    const preview = prompt?.slice(0, 480).split("\n").slice(0, 8).join("\n");
    const long = prompt !== preview;
    return (
      <div
        className="flex min-w-0 w-full items-start gap-2.5 px-3 py-2 text-sm"
        data-testid="step-plan-trace"
        data-plan-id={trace.planId}
      >
        <ListChecks size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium text-muted">
            {trace.action === "Linked" ? "Plan linked to Step by step" : "Request sent to Step by step"}
          </div>
          <StepPlanLink note={trace} />
          <div className="mt-2.5 border-l border-border pl-3 text-muted" data-testid="step-plan-prompt">
            {prompt != null ? (
              <>
                <div className="mb-1.5 text-xs font-medium">
                  Original prompt{trace.snapshot?.source === "topic_message" ? " · From topic message" : " · Submitted"}
                </div>
                <div id={promptId} className="whitespace-pre-wrap [overflow-wrap:anywhere] leading-relaxed">
                  {expanded ? prompt : preview}{long && !expanded ? "…" : ""}
                </div>
                {long && (
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-controls={promptId}
                    onClick={() => setExpanded((value) => !value)}
                    className="mt-2 rounded text-xs font-medium text-accent hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {expanded ? "Show less" : "Show all"}
                  </button>
                )}
              </>
            ) : (
              <p className="text-xs">
                {trace.action === "Linked"
                  ? "No original prompt snapshot was saved with this link."
                  : "The original prompt was not recorded with this older trace."}
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }
  // Plan-chat activity notes still use the compact notice presentation.
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="inline-flex max-w-full items-start gap-1.5 rounded-lg border border-accent/30 bg-accent/[0.06] px-3 py-1.5 text-sm text-fg">
        <ListChecks size={14} className="mt-0.5 shrink-0 text-accent" aria-hidden />
        <Markdown className="min-w-0 break-words text-sm">{content}</Markdown>
      </div>
    </div>
  );
}
