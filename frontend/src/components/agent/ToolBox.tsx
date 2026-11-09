import { useState } from "react";
import { ChevronDown, Loader2, ShieldQuestion } from "lucide-react";
import { toolStatus } from "../../lib/agentNodeModel";
import type { ToolStep } from "../../lib/agentWorkflow";
import type { AgentPermissionDecisionValue } from "../../lib/types";
import { PermissionBody } from "../AgentPermissionBody";
import { CATEGORY_STYLE, toolIcon } from "../AgentTimeline";

export type DecisionHandler = (
  requestId: string,
  decision: AgentPermissionDecisionValue,
) => void | Promise<void>;

function DetailToggle({ open, onToggle, size, labels, className }: {
  open: boolean;
  onToggle: () => void;
  size: number;
  labels: { open: string; closed: string; tooltipOpen: string; tooltipClosed: string };
  className: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={className}
      data-tooltip={open ? labels.tooltipOpen : labels.tooltipClosed}
    >
      <ChevronDown size={size} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      {open ? labels.open : labels.closed}
    </button>
  );
}

function ToolField({ label, value }: { label: string; value: string }) {
  const [expanded, setExpanded] = useState(false);
  // Collapsed keeps the box compact (a short scrollable window) and hard-caps
  // very long blobs so they can't freeze layout — but that also means content
  // past the cap can't be reached by scrolling. Expanding lifts both limits and
  // wraps long lines so the whole input/output is visible on demand.
  const LIMIT = 2000;
  const clipped = value.length > LIMIT || value.split("\n").length > 12;
  const shown = expanded || value.length <= LIMIT ? value : `${value.slice(0, LIMIT)}…`;
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <div className="text-[9px] font-medium uppercase tracking-wide text-muted">{label}</div>
        {clipped && (
          <DetailToggle
            open={expanded}
            onToggle={() => setExpanded((v) => !v)}
            size={11}
            labels={{ open: "Collapse", closed: "Expand", tooltipOpen: "Collapse", tooltipClosed: "Show full content" }}
            className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium uppercase tracking-wide text-muted hover:bg-bg"
          />
        )}
      </div>
      <pre
        className={`mt-0.5 overflow-auto rounded bg-bg px-2 py-1 font-mono text-[10px] leading-snug ${
          expanded ? "whitespace-pre-wrap break-words" : "max-h-48"
        }`}
      >
        {shown}
      </pre>
    </div>
  );
}

// A grouped tool call. Shows the tool name + status; input/output collapse until
// the user wants to see "what was done"; a pending approval renders inline.
export function ToolBox({
  step,
  busy,
  onDecision,
  closed = false,
}: {
  step: ToolStep;
  busy: boolean;
  onDecision: DecisionHandler;
  /**
   * The event stream this box belongs to has ended (a finished workflow step
   * attempt). Nothing can still be running in it, so an unterminated call is
   * reported for what it is rather than left spinning forever.
   */
  closed?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(step.input || step.output);
  const status = toolStatus(step, closed);
  const colours = status.alert ? CATEGORY_STYLE.permission : CATEGORY_STYLE.tool;
  let icon = toolIcon(step.toolName);
  if (status.running) icon = <Loader2 size={13} className="animate-spin" />;
  else if (status.alert) icon = <ShieldQuestion size={13} />;

  return (
    <div
      className={`w-full max-w-xl rounded-lg border p-2.5 transition hover:border-accent hover:ring-2 hover:ring-accent/40 ${colours.box}`}
    >
      <div className="flex items-center gap-2">
        <span className={`grid h-6 w-6 shrink-0 place-items-center rounded-full border ${colours.marker}`}>{icon}</span>
        <span className="truncate text-[11px] font-semibold">{step.toolName || "Tool"}</span>
        <span className={`text-[10px] ${status.stale ? "text-orange-500" : "text-muted"}`}>{status.label}</span>
        {hasDetail && (
          <DetailToggle
            open={open}
            onToggle={() => setOpen((v) => !v)}
            size={12}
            labels={{ open: "Hide", closed: "Details", tooltipOpen: "Hide details", tooltipClosed: "Show what was done" }}
            className="ml-auto flex items-center gap-0.5 rounded px-1 py-0.5 text-[10px] text-muted hover:bg-bg"
          />
        )}
      </div>

      {open && hasDetail && (
        <div className="mt-1.5 space-y-1.5">
          {step.input && <ToolField label="input" value={step.input} />}
          {step.output && <ToolField label="output" value={step.output} />}
        </div>
      )}

      {step.pending && (
        <PermissionBody
          data={step.pending.data}
          requestId={step.pending.requestId}
          busy={busy}
          onDecision={onDecision}
        />
      )}
    </div>
  );
}
