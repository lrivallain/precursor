import { Settings as SettingsIcon, Workflow as WorkflowIcon } from "lucide-react";
import type { Workflow } from "../lib/types";
import { WorkflowList } from "./WorkflowList";
import { SectionLoading } from "./SectionLoading";
import { WorkflowView } from "./WorkflowView";
import { WorkflowBuilder } from "./WorkflowBuilder";

interface Props {
  enabled: boolean;
  /**
   * Settings haven't resolved yet, so `enabled` is not yet meaningful. Without
   * it, opening Workflows cold flashes the "needs Agents mode" prompt before
   * the gallery loads.
   */
  ready?: boolean;
  workflows: Workflow[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  onChanged: (workflow: Workflow) => void;
  onDeleted: (id: number) => void;
  /** Deep-link target from the route (`/workflows/<id>`); null shows the gallery. */
  activeId: number | null;
  editor: { id: number | null } | null;
  onEdit: (id: number | null) => void;
  onCloseEditor: () => void;
  /** Run segment from the route (`/run/<n|latest>`); null when absent. */
  runSeg: string | null;
  onNavigate: (id: number | null) => void;
  /** Reports the currently-shown run segment back up to drive the URL. */
  onRunSegChange: (seg: string | null) => void;
  /** Opens Settings on Agents — the toggle this section is gated behind. */
  onOpenSettings: () => void;
  onOpenAgent: (agentId: number) => void;
}

/**
 * Workflows main pane. The shell owns collection and selection so sidebar,
 * cards, deep links and browser history all address the same view.
 */
export function WorkflowsSection({
  enabled,
  ready = true,
  workflows,
  loading,
  error,
  onReload,
  onChanged,
  onDeleted,
  activeId,
  editor,
  onEdit,
  onCloseEditor,
  runSeg,
  onNavigate,
  onRunSegChange,
  onOpenSettings,
  onOpenAgent,
}: Props) {
  // Feature state unknown: say nothing rather than advertising the section as
  // unavailable for the width of the settings request.
  if (!ready) {
    return <SectionLoading label="Loading workflows…" />;
  }

  if (!enabled) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <WorkflowIcon size={28} className="text-muted" />
        <div className="max-w-sm space-y-1">
          <p className="text-sm font-medium">Workflows need Agents mode</p>
          <p className="text-[12px] text-muted">
            A workflow chains agents into a repeatable pipeline, so it runs on the same
            runtime. Turn Agents on in Settings to get started.
          </p>
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm text-white"
        >
          <SettingsIcon size={14} /> Open Settings
        </button>
      </div>
    );
  }

  if (loading) return <SectionLoading label="Loading workflows..." />;

  if (error && workflows.length === 0) {
    return (
      <div role="alert" className="p-6 text-sm">
        <p>{error}</p>
        <button type="button" onClick={onReload} className="mt-3 rounded border border-border px-3 py-1.5 hover:bg-surface">
          Retry
        </button>
      </div>
    );
  }

  const active = workflows.find((w) => w.id === activeId) ?? null;

  if (editor) {
    const editing = workflows.find((w) => w.id === editor.id) ?? null;
    return (
      <WorkflowBuilder
        key={editor.id ?? "new"}
        workflow={editing}
        onSaved={(wf) => {
          onChanged(wf);
          onNavigate(wf.id);
        }}
        onCancel={onCloseEditor}
      />
    );
  }

  if (active) {
    return (
      <WorkflowView
        key={active.id}
        workflow={active}
        initialRunSeg={runSeg}
        onRunSegChange={onRunSegChange}
        onBack={() => onNavigate(null)}
        onEdit={() => onEdit(active.id)}
        onChanged={onChanged}
        onDeleted={() => {
          onDeleted(active.id);
          onNavigate(null);
        }}
        onOpenInAgents={onOpenAgent}
      />
    );
  }

  if (activeId != null) {
    return (
      <div className="p-6 text-sm">
        <p>Workflow not found.</p>
        <button type="button" onClick={() => onNavigate(null)} className="mt-3 rounded border border-border px-3 py-1.5 hover:bg-surface">
          Back to overview
        </button>
      </div>
    );
  }

  return (
    <WorkflowList
      workflows={workflows}
      loading={loading}
      onOpen={(wf) => onNavigate(wf.id)}
      onNew={() => onEdit(null)}
      onImported={(result) => {
        onReload();
        if (result.workflow_id != null) {
          onNavigate(result.workflow_id);
        }
      }}
      onChanged={onChanged}
    />
  );
}
