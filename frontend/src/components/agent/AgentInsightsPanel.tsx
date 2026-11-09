import { BarChart3, Check, ChevronLeft, ChevronRight, Eye, X } from "lucide-react";
import type { ShowPrefs } from "../../lib/agentShowPrefs";
import { usePersistedState } from "../../lib/resizeCore";
import type { AgentArtifact, AgentEvent, AgentRun, AgentSession } from "../../lib/types";
import { AgentUsageSection } from "../AgentUsage";
import { AgentOrchestrationSection } from "./AgentOrchestrationSection";
import { AgentRunsSection } from "./AgentRunsSection";

const SHOW_PANEL_KEY = "precursor:agent-show-panel:collapsed";
// Hidden by default — only stay open when the user explicitly expanded it.
const parseCollapsed = (raw: string): boolean => raw !== "0";
const serializeCollapsed = (collapsed: boolean): string => (collapsed ? "1" : "0");

const SHOW_TOGGLES: { key: keyof ShowPrefs; label: string }[] = [
  { key: "system", label: "System" },
  { key: "assistant", label: "Assistant" },
  { key: "thinking", label: "Thinking" },
  { key: "tool", label: "Tool" },
  { key: "lifecycle", label: "Lifecycle" },
  { key: "fold", label: "Fold finished turns" },
];

// A small on/off filter pill for the workflow display toggles.
function ToggleChip({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition ${
        active ? "border-accent/40 bg-accent/15 text-accent" : "border-border text-muted hover:text-fg"
      }`}
    >
      {active ? <Check size={10} /> : <X size={10} className="opacity-50" />}
      {label}
    </button>
  );
}

export interface AgentInsightsPanelProps {
  compact?: { busy: boolean; disabled: boolean; disabledReason?: string; run: () => void };
  showPrefs: ShowPrefs;
  toggleShow: (k: keyof ShowPrefs) => void;
  events: AgentEvent[];
  model: string | null;
  agent: AgentSession;
  runs: AgentRun[];
  runFilter: number | null;
  onRunFilterChange: (choice: number | "all") => void;
  artifacts: AgentArtifact[];
  artifactLabel: (a: AgentArtifact) => string | null;
  onOpenArtifact: (a: AgentArtifact) => void;
}

// Collapsible right-hand panel mirroring the conversation-stats aside on
// topics/chats: a token-usage readout plus the workflow "Show" filters.
// Collapse state is persisted.
export function AgentInsightsPanel(props: AgentInsightsPanelProps) {
  const { showPrefs, toggleShow, agent } = props;
  const [collapsed, setCollapsed] = usePersistedState(SHOW_PANEL_KEY, true, parseCollapsed, serializeCollapsed);

  if (collapsed) {
    return (
      <aside className="flex w-9 shrink-0 flex-col items-center border-l border-border bg-surface/40 px-1 py-2">
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="rounded p-1.5 text-muted hover:bg-surface"
          data-tooltip="Show usage & filters"
          aria-label="Show usage and workflow filters"
        >
          <ChevronLeft size={16} />
        </button>
        <BarChart3 size={16} className="mt-2 text-muted" />
        <Eye size={16} className="mt-2 text-muted" />
      </aside>
    );
  }

  return (
    <aside className="flex w-60 shrink-0 flex-col border-l border-border bg-surface/30">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <span>Insights</span>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          className="rounded p-1 text-muted hover:bg-surface"
          data-tooltip="Collapse panel"
          aria-label="Collapse panel"
        >
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="flex flex-col gap-4 overflow-y-auto p-3">
        <AgentUsageSection events={props.events} model={props.model} compact={props.compact} />
        <div className="border-t border-border" />
        <AgentRunsSection agent={agent} runs={props.runs} selected={props.runFilter} onSelect={props.onRunFilterChange} />
        <div className="border-t border-border" />
        <AgentOrchestrationSection
          agentId={agent.id}
          artifacts={props.artifacts}
          artifactLabel={props.artifactLabel}
          onOpenArtifact={props.onOpenArtifact}
        />
        <div className="border-t border-border" />
        <div className="space-y-2">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Eye size={14} />
            <span>Show</span>
          </div>
          <div className="flex flex-col items-start gap-1.5">
            {SHOW_TOGGLES.map(({ key, label }) => (
              <ToggleChip key={key} active={showPrefs[key]} onClick={() => toggleShow(key)} label={label} />
            ))}
          </div>
        </div>
      </div>
    </aside>
  );
}
