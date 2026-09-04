import { useCallback, useEffect, useRef, useState } from "react";
import { Settings as SettingsIcon, Workflow as WorkflowIcon } from "lucide-react";
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
  /** Opens Settings on Agents — the toggle this section is gated behind. */
  onOpenSettings: () => void;
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
  onOpenSettings,
  onOpenAgent,
}: Props) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>({ kind: "list" });
  const loadedRef = useRef(false);

  // `silent` refetches without flipping the spinner — a background refresh must
  // not blank the gallery the user is watching. Only the first load has nothing
  // to show yet.
  const load = useCallback(
    async (silent = false) => {
      if (!enabled) return;
      if (!silent) setLoading(true);
      try {
        const items = await api.workflows.list();
        setWorkflows(items);
        loadedRef.current = true;
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [enabled],
  );

  useEffect(() => {
    void load(loadedRef.current);
  }, [load, reloadKey]);

  // `workflow.changed` only fires when the pipeline *advances a step*, so a step
  // that runs for minutes leaves every gallery bar frozen. Poll while a run is
  // actually executing to pick up the in-step agent progress the bars blend in.
  // Paused and awaiting-approval runs make no progress of their own — SSE
  // already covers the moment they move — so an otherwise idle gallery is free.
  const anyRunning = workflows.some((w) => w.status === "running");
  useEffect(() => {
    if (!enabled || !anyRunning || mode.kind !== "list") return;
    const t = window.setInterval(() => void load(true), 2000);
    return () => window.clearInterval(t);
  }, [enabled, anyRunning, mode.kind, load]);

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
      onImported={(result) => {
        void load();
        if (result.workflow_id != null) {
          onNavigate(result.workflow_id);
          setMode({ kind: "view", id: result.workflow_id });
        }
      }}
      onChanged={upsert}
    />
  );
}
