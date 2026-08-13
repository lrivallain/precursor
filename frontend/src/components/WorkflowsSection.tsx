import { useCallback, useEffect, useState } from "react";
import { Workflow as WorkflowIcon } from "lucide-react";
import { api } from "../lib/api";
import type { Workflow } from "../lib/types";
import { WorkflowList } from "./WorkflowList";
import { WorkflowView } from "./WorkflowView";
import { WorkflowBuilder } from "./WorkflowBuilder";

interface Props {
  enabled: boolean;
  /** Bumped by App on `workflow.changed` SSE to force a reload. */
  reloadKey: number;
  /** Deep-link target from the route (`/workflows/<id>`); null shows the gallery. */
  activeId: number | null;
  /** Bumped by App when the "New workflow" action fires. */
  newSignal: number;
  /** Run segment from the route (`/run/<n|latest>`); null when absent. */
  runSeg: string | null;
  onNavigate: (id: number | null) => void;
  /** Reports the currently-shown run segment back up to drive the URL. */
  onRunSegChange: (seg: string | null) => void;
  onOpenAgent: (agentId: number) => void;
}

type Mode = { kind: "list" } | { kind: "view"; id: number } | { kind: "builder"; id: number | null };

/**
 * Top-level Workflows cockpit. Owns the workflow collection and routes between
 * the gallery, the detail board, and the create/edit builder. Mirrors the
 * agents section's shape but is fully decoupled from topics.
 */
export function WorkflowsSection({
  enabled,
  reloadKey,
  activeId,
  newSignal,
  runSeg,
  onNavigate,
  onRunSegChange,
  onOpenAgent,
}: Props) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>({ kind: "list" });

  const load = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    try {
      const items = await api.workflows.list();
      setWorkflows(items);
    } finally {
      setLoading(false);
    }
  }, [enabled]);

  useEffect(() => {
    void load();
  }, [load, reloadKey]);

  // Route-driven active workflow: sync the deep-link id into local mode.
  useEffect(() => {
    if (activeId != null) {
      setMode((m) => (m.kind === "view" && m.id === activeId ? m : { kind: "view", id: activeId }));
    } else {
      setMode((m) => (m.kind === "builder" ? m : { kind: "list" }));
    }
  }, [activeId]);

  // "New workflow" trigger from the header / command palette.
  useEffect(() => {
    if (newSignal > 0) setMode({ kind: "builder", id: null });
  }, [newSignal]);

  const upsert = useCallback((wf: Workflow) => {
    setWorkflows((prev) => {
      const idx = prev.findIndex((w) => w.id === wf.id);
      if (idx === -1) return [wf, ...prev];
      const next = [...prev];
      next[idx] = wf;
      return next;
    });
  }, []);

  if (!enabled) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
        <WorkflowIcon size={22} className="text-muted" />
        <p className="text-sm text-muted">
          Workflows require Agents. Enable it in Settings → Agents.
        </p>
      </div>
    );
  }

  const active =
    mode.kind === "view" ? workflows.find((w) => w.id === mode.id) ?? null : null;

  if (mode.kind === "builder") {
    const editing = mode.id != null ? workflows.find((w) => w.id === mode.id) ?? null : null;
    return (
      <WorkflowBuilder
        workflow={editing}
        onSaved={(wf) => {
          upsert(wf);
          onNavigate(wf.id);
          setMode({ kind: "view", id: wf.id });
        }}
        onCancel={() => {
          if (editing) {
            setMode({ kind: "view", id: editing.id });
          } else {
            onNavigate(null);
            setMode({ kind: "list" });
          }
        }}
      />
    );
  }

  if (mode.kind === "view" && active) {
    return (
      <WorkflowView
        key={active.id}
        workflow={active}
        initialRunSeg={runSeg}
        onRunSegChange={onRunSegChange}
        onBack={() => {
          onNavigate(null);
          setMode({ kind: "list" });
        }}
        onEdit={() => setMode({ kind: "builder", id: active.id })}
        onChanged={upsert}
        onDeleted={() => {
          setWorkflows((prev) => prev.filter((w) => w.id !== active.id));
          onNavigate(null);
          setMode({ kind: "list" });
        }}
        onOpenInAgents={onOpenAgent}
      />
    );
  }

  return (
    <WorkflowList
      workflows={workflows}
      loading={loading}
      onOpen={(wf) => {
        onNavigate(wf.id);
        setMode({ kind: "view", id: wf.id });
      }}
      onNew={() => setMode({ kind: "builder", id: null })}
      onChanged={upsert}
    />
  );
}
